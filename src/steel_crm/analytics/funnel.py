"""The Dynamics 365 sales funnel: Lead → Opportunity → Quote → Order → Invoice,
ending in Won or Lost, with how many records drop out between two steps and why.

Each step is a Dataverse record type, so the funnel reads straight off the
export:

* Lead: every `lead` row. It moves on when qualified (statecode Qualified);
  otherwise it drops out with its status reason (Lost, Cannot Contact, No
  Longer Interested, Canceled) or is still being worked.
* Opportunity: the opportunity a qualified lead became. It moves on once a
  `quote` exists; otherwise it was lost before any quote, or is still open.
* Quote: opportunities with at least one quote. They move on when the deal
  is won; otherwise they were lost for the reason in `ahn_lossreason`, or
  are still being negotiated.
* Order: the `salesorder` of each won deal. It moves on once delivered and
  invoiced.
* Invoice: the `invoice` of each delivered order, split into paid, open and
  overdue.

Two views: new business (the funnel starts at the lead) and all
opportunities (repeat business from existing customers has no lead, so that
view starts at the opportunity).
"""

from __future__ import annotations

import pandas as pd

from ..warehouse.star_schema import Warehouse

# step: (name, what a record at this step is, Dataverse table)
STEPS = {
    "lead": ("Lead", "Prospect captured", "lead"),
    "opportunity": ("Opportunity", "Qualified deal", "opportunity"),
    "quote": ("Quote", "Priced offer sent", "quote"),
    "order": ("Order", "Deal won, order booked", "salesorder"),
    "invoice": ("Invoice", "Delivered and billed", "invoice"),
}


def _reasons(labels: pd.Series, values: pd.Series | None = None) -> list[dict]:
    df = pd.DataFrame(
        {
            "reason": labels.fillna("Not recorded").to_numpy(),
            "value": 0.0 if values is None else values.fillna(0).to_numpy(),
        }
    )
    g = df.groupby("reason").agg(count=("reason", "size"), value=("value", "sum"))
    g = g.sort_values("count", ascending=False)
    return [{"reason": r, "count": int(x["count"]), "value": float(x["value"])} for r, x in g.iterrows()]


def _step(key: str, count: int, value: float | None = None, tons: float | None = None, **extra) -> dict:
    name, note, entity = STEPS[key]
    return {
        "key": key,
        "name": name,
        "note": note,
        "entity": entity,
        "count": int(count),
        "value": None if value is None else float(value),
        "tons": None if tons is None else float(tons),
        **extra,
    }


def _segment(wh: Warehouse, opps: pd.DataFrame, leads: pd.DataFrame | None) -> dict:
    steps, leaks = [], []
    if leads is not None:
        steps.append(_step("lead", len(leads), tons=leads["estimated_tons"].sum()))
        dropped = leads[leads["status"] != "Qualified"]
        reason = dropped["status_reason"].where(dropped["status"] == "Disqualified", "Still open")
        leaks.append({"after": "lead", "count": int(len(dropped)), "reasons": _reasons(reason)})

    steps.append(_step("opportunity", len(opps), opps["estimated_value"].sum(), opps["tons"].sum()))
    quoted, unquoted = opps[opps["n_quotes"] > 0], opps[opps["n_quotes"] == 0]
    reason = unquoted["loss_reason"].where(unquoted["state"] == "Lost", "Open, not quoted yet")
    leaks.append(
        {"after": "opportunity", "count": int(len(unquoted)), "reasons": _reasons(reason, unquoted["estimated_value"])}
    )

    steps.append(
        _step(
            "quote",
            len(quoted),
            quoted["estimated_value"].sum(),
            quoted["tons"].sum(),
            quotes_issued=int(quoted["n_quotes"].sum()),
        )
    )
    won = quoted[quoted["state"] == "Won"]
    not_won = quoted[quoted["state"] != "Won"]
    reason = not_won["loss_reason"].where(not_won["state"] == "Lost", "Open, still negotiating")
    leaks.append(
        {"after": "quote", "count": int(len(not_won)), "reasons": _reasons(reason, not_won["estimated_value"])}
    )

    lines = wh.fact_sales_line[wh.fact_sales_line["opportunityid"].isin(won["opportunityid"])]
    orders = lines.groupby("salesorderid").agg(value=("net", "sum"), tons=("tons", "sum"))
    steps.append(_step("order", len(orders), orders["value"].sum(), orders["tons"].sum()))
    inv = wh.fact_invoice[wh.fact_invoice["salesorderid"].isin(orders.index)]
    waiting = orders[~orders.index.isin(inv["salesorderid"])]
    leaks.append(
        {
            "after": "order",
            "count": int(len(waiting)),
            "reasons": (
                _reasons(pd.Series(["Awaiting delivery"] * len(waiting), dtype=object), waiting["value"])
                if len(waiting)
                else []
            ),
        }
    )

    overdue = inv["is_open"] & (inv["due_date"] < wh.as_of)
    status = pd.Series("Paid", index=inv.index).mask(inv["is_open"], "Open, not due").mask(overdue, "Overdue")
    steps.append(_step("invoice", len(inv), inv["amount"].sum(), payment=_reasons(status, inv["amount"])))

    closed = opps[opps["state"] != "Open"]
    lost = opps[opps["state"] == "Lost"]
    open_ = opps[opps["state"] == "Open"]
    outcome = {
        "won": int((opps["state"] == "Won").sum()),
        "won_value": float(opps.loc[opps["state"] == "Won", "actual_value"].sum()),
        "lost": int(len(lost)),
        "lost_value": float(lost["estimated_value"].sum()),
        "open": int(len(open_)),
        "open_value": float(open_["estimated_value"].sum()),
        "win_rate": float((closed["state"] == "Won").mean()) if len(closed) else None,
        "lost_reasons": _reasons(lost["loss_reason"], lost["estimated_value"]),
    }
    return {"steps": steps, "leaks": leaks, "outcome": outcome}


def sales_funnel(wh: Warehouse) -> dict:
    opps = wh.fact_opportunity
    new = opps[opps["originatingleadid"].notna()]
    return {
        "from": wh.fact_lead["created"].min(),
        "to": wh.as_of,
        "new_business": _segment(wh, new, wh.fact_lead),
        "all": _segment(wh, opps, None),
    }
