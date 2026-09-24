"""After the sale: delivery, cash collection and customer service, measured on the
same Dynamics export, plus the numbers that go on each step of the BPMN diagrams.

Delivery is judged the way a customer judges it: on time (delivered by the
promised date, `salesorder.requestdeliveryby`), in full (the weighbridge
weight within 0.5% of the ordered tons) and both (OTIF). The time from order
to delivery is split into the steps the shipment table records: stock ready
(or the mill releases the load), waiting in the yard for a truck, loading
and waybill, and the road.

Customer service follows Dynamics 365 Customer Service: a case has a first
response and a resolution SLA set by its priority, may be escalated, and a
claim is either upheld (with a credit note or replacement) or explained.

Everything here covers the last 12 months: orders by order date, invoices by
invoice date, cases by the day they were opened.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..warehouse.star_schema import WEIGHT_TOLERANCE, Warehouse
from .accounts import at_risk
from .sales import _in, windows

PHASES = [("hours_to_ready", "Stock ready / mill releases"), ("yard_hours", "Waiting for a truck"),
          ("loading_hours", "Loading and waybill"), ("transit_hours", "On the road")]


def _share(mask: pd.Series) -> float | None:
    mask = mask.dropna()
    return float(mask.astype(bool).mean()) if len(mask) else None


def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.median()) if len(s) else None


def _delivered(wh: Warehouse) -> pd.DataFrame:
    l12, _, end = windows(wh.as_of)
    s = wh.fact_shipment
    return s[_in(s["order_date"], l12, end) & s["delivered"].notna()]


def delivery(wh: Warehouse) -> dict:
    d = _delivered(wh)
    stock, mill = d[d["sourcing"] == "From stock"], d[d["sourcing"] != "From stock"]
    kpi = {
        "shipments": int(len(d)), "tons": float(d["ordered_tons"].sum()),
        "truckloads": int(d["truckloads"].sum()),
        "on_time": _share(d["on_time"]), "in_full": _share(d["in_full"]), "otif": _share(d["otif"]),
        "days_to_deliver": _median(d["days_to_deliver"]),
        "from_stock": float(len(stock) / len(d)) if len(d) else None,
        "pod": _share(d["pod"]), "outside_tolerance": 1 - (_share(d["in_full"]) or 0),
        "yard_hours": _median(d["yard_hours"]), "stock_ready_hours": _median(stock["hours_to_ready"]),
        "mill_days": (_median(mill["hours_to_ready"]) or 0) / 24,
        "transit_hours": _median(d["transit_hours"]), "loading_hours": _median(d["loading_hours"]),
        "promise_days": _median((d["promised"] - d["order_date"].dt.normalize()).dt.days),
        "weight_variance": _median(d["weight_variance"].abs()),
    }

    def summary(g: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "shipments": len(g), "tons": g["ordered_tons"].sum(), "days": g["days_to_deliver"].median(),
            "on_time": _share(g["on_time"]), "in_full": _share(g["in_full"]), "otif": _share(g["otif"]),
            "yard_hours": g["yard_hours"].median(), "weight_off": g["weight_variance"].abs().median(),
            **{col: g[col].median() for col, _ in PHASES},
        })

    by_sourcing = d.groupby("sourcing").apply(summary, include_groups=False).reset_index()
    by_carrier = d.groupby("carrier").apply(summary, include_groups=False).reset_index()
    by_carrier = by_carrier.sort_values("shipments", ascending=False)
    by_warehouse = d.groupby("warehouse").apply(summary, include_groups=False).reset_index()
    by_warehouse = by_warehouse.sort_values("shipments", ascending=False)

    edges = np.round(np.arange(-0.015, 0.0151, 0.0025), 4)
    v = d["weight_variance"].clip(edges[0], edges[-1] - 1e-9)
    counts = pd.cut(v, edges, right=False).value_counts(sort=False)
    weight_hist = [{"from": float(iv.left), "to": float(iv.right), "loads": int(n)} for iv, n in counts.items()]

    s = wh.fact_shipment[wh.fact_shipment["delivered"].notna()]
    monthly = s.groupby("month").agg(shipments=("shipmentid", "count"), on_time=("on_time", _share),
                                     otif=("otif", _share)).reset_index()
    return {"kpi": kpi, "by_sourcing": by_sourcing, "by_carrier": by_carrier, "by_warehouse": by_warehouse,
            "weight_hist": weight_hist, "monthly": monthly, "tolerance": WEIGHT_TOLERANCE}


def collections(wh: Warehouse) -> dict:
    l12, _, end = windows(wh.as_of)
    inv = wh.fact_invoice
    i = inv[_in(inv["invoice_date"], l12, end)]
    paid = i[~i["is_open"]]
    overdue_now = i["is_open"] & (i["due_date"] < wh.as_of)
    late = paid["days_late"] > 0
    open_old = inv[inv["is_open"] & ((wh.as_of - inv["due_date"]).dt.days > 30)]
    return {
        "invoices": int(len(i)), "amount": float(i["amount"].sum()),
        "paid_on_time": float((~late).sum() / len(i)) if len(i) else None,
        "paid_late_or_overdue": float((late.sum() + overdue_now.sum()) / len(i)) if len(i) else None,
        "days_late_median": _median(paid.loc[late, "days_late"]),
        "over_30_late": float(((paid["days_late"] > 30).sum() + ((wh.as_of - i.loc[overdue_now, "due_date"])
                                                                 .dt.days > 30).sum()) / len(i)) if len(i) else None,
        "days_to_pay": _median(paid["days_to_pay"]),
        "accounts_over_30": int(open_old["accountid"].nunique()),
        "amount_over_30": float(open_old["amount"].sum()),
    }


def service(wh: Warehouse) -> dict:
    l12, _, end = windows(wh.as_of)
    c = wh.fact_case
    cl = c[_in(c["created"], l12, end)]
    deliveries = len(_delivered(wh))
    problems = cl[(cl["case_type"] == "Problem") & cl["upheld"].notna()]
    kpi = {
        "cases": int(len(cl)), "per_100_deliveries": float(len(cl) / deliveries * 100) if deliveries else None,
        "response_sla": _share(cl["response_sla_met"]), "resolve_sla": _share(cl["resolve_sla_met"]),
        "first_response_hours": _median(cl["hours_to_first_response"]),
        "resolve_hours": _median(cl["hours_to_resolve"]), "escalated": _share(cl["escalated"]),
        "upheld": _share(problems["upheld"]), "compensation": float(cl["compensation"].fillna(0).sum()),
        "csat": float(cl["csat"].mean()) if cl["csat"].notna().any() else None,
        "csat_responses": _share(cl["csat"].notna()), "open": int(cl["is_open"].sum()),
    }
    unhappy = cl[cl["csat"] <= 2]
    kpi["unhappy_after_bad_claim"] = _share((unhappy["resolve_sla_met"] == False)  # noqa: E712
                                            | (unhappy["upheld"] == False))  # noqa: E712
    by_category = cl.groupby("category").agg(
        cases=("incidentid", "count"), team=("team", "first"), hours=("hours_to_resolve", "median"),
        resolve_sla=("resolve_sla_met", _share), upheld=("upheld", _share),
        compensation=("compensation", "sum"), csat=("csat", "mean")).reset_index()
    by_category["share"] = by_category["cases"] / by_category["cases"].sum()
    by_category = by_category.sort_values("cases", ascending=False)
    by_agent = cl.groupby("owner").agg(
        cases=("incidentid", "count"), first_response=("hours_to_first_response", "median"),
        response_sla=("response_sla_met", _share), resolve_sla=("resolve_sla_met", _share),
        csat=("csat", "mean")).reset_index().sort_values("cases", ascending=False)
    csat = cl["csat"].dropna().astype(int).value_counts().reindex(range(1, 6), fill_value=0)

    open_cases = c[c["is_open"]].copy()
    open_cases["age_days"] = (wh.as_of + pd.Timedelta(days=1) - open_cases["created"]).dt.days
    open_cases["breached"] = open_cases["resolve_by"] < wh.as_of
    open_cases = open_cases.merge(wh.dim_account[["accountid", "name"]], on="accountid")
    open_cases = open_cases.sort_values(["breached", "age_days"], ascending=False)
    return {"kpi": kpi, "by_category": by_category, "by_agent": by_agent,
            "csat": [{"score": int(k), "responses": int(v)} for k, v in csat.items()],
            "open": open_cases, "churn": service_and_churn(wh)}


def service_and_churn(wh: Warehouse) -> dict:
    """Did regular customers whose claim broke its SLA stop ordering more often?"""
    lines = wh.fact_sales_line
    orders = lines.groupby("accountid")["salesorderid"].nunique()
    regular = set(orders[orders >= 3].index)
    risk, _ = at_risk(wh, n=100_000)
    quiet = set(risk["accountid"])
    claims = wh.fact_case[(wh.fact_case["case_type"] == "Problem") & wh.fact_case["accountid"].isin(regular)]
    breached = set(claims.loc[claims["resolve_sla_met"] == False, "accountid"])  # noqa: E712
    handled = set(claims["accountid"]) - breached
    none = regular - breached - handled

    def rate(group: set) -> dict:
        return {"customers": len(group), "quiet": len(group & quiet),
                "rate": len(group & quiet) / len(group) if group else None}

    return {"breached": rate(breached), "within_sla": rate(handled), "no_claim": rate(none)}


def lead_to_cash(wh: Warehouse) -> dict:
    """Median days in each phase from a new lead to cash in the bank, last 12 months."""
    l12, _, end = windows(wh.as_of)
    lead = wh.fact_lead[_in(wh.fact_lead["created"], l12, end)]
    qualified = lead[lead["status"] == "Qualified"]
    opp = wh.fact_opportunity[_in(wh.fact_opportunity["created"], l12, end)]
    won = opp[opp["state"] == "Won"]
    d = _delivered(wh)
    inv = wh.fact_invoice[_in(wh.fact_invoice["invoice_date"], l12, end) & ~wh.fact_invoice["is_open"]]
    phases = [
        ("Lead to qualified", _median((qualified["decided"] - qualified["created"]).dt.total_seconds() / 86400)),
        ("Opportunity to first quote", _median(opp["hours_to_first_quote"]) / 24),
        ("First quote to won", _median((won["close_date"] - won["first_quote_at"].dt.normalize()).dt.days)),
        ("Order to delivery", _median(d["days_to_deliver"])),
        ("Invoice to payment", _median(inv["days_to_pay"].clip(lower=0))),
    ]
    # the whole chain for new business: lead created -> invoice paid
    chain = (wh.fact_lead[["leadid", "created", "opportunityid"]].dropna(subset=["opportunityid"])
             .merge(wh.fact_sales_line[["opportunityid", "salesorderid"]].drop_duplicates(), on="opportunityid")
             .merge(wh.fact_invoice[["salesorderid", "paid_on"]], on="salesorderid"))
    chain = chain[chain["paid_on"].notna() & _in(chain["paid_on"], l12, end)]
    total = _median((chain["paid_on"] - chain["created"]).dt.total_seconds() / 86400)
    return {"phases": [{"phase": p, "days": v} for p, v in phases], "end_to_end_days": total,
            "end_to_end_deals": int(len(chain))}


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"


def _num(x: float) -> str:
    return f"{x:,.0f}"


def _dur(hours: float | None) -> str:
    if hours is None:
        return "—"
    return f"{hours:.1f} h" if hours < 10 else f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.1f} days"


def _irr(v: float) -> str:
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            x = v / div
            return f"{x:,.0f}{unit} IRR" if abs(x) >= 100 else f"{x:,.1f}{unit} IRR"
    return f"{v:,.0f} IRR"


def process_overlays(wh: Warehouse, dlv: dict, cash: dict, svc: dict, ar: dict) -> dict[str, dict]:
    """What the export says about each BPMN step: {node id: {value, note, tone}}."""
    l12, _, end = windows(wh.as_of)
    lead = wh.fact_lead[_in(wh.fact_lead["created"], l12, end)]
    decided = lead[lead["decided"].notna()]
    opp = wh.fact_opportunity[_in(wh.fact_opportunity["created"], l12, end)]
    quoted = opp[opp["n_quotes"] > 0]
    closed = quoted[quoted["state"] != "Open"]
    lost = opp[opp["state"] == "Lost"]
    lines = wh.fact_sales_line[_in(wh.fact_sales_line["order_date"], l12, end)]
    dk, sk = dlv["kpi"], svc["kpi"]
    cat = svc["by_category"].set_index("category")

    def cases(*names):
        sub = cat.reindex([n for n in names if n in cat.index])
        n = int(sub["cases"].sum())
        hours = wh.fact_case[_in(wh.fact_case["created"], l12, end)
                             & wh.fact_case["category"].isin(names)]["hours_to_resolve"]
        return n, _median(hours)

    o = {}

    def put(node, value, note, tone=""):
        o[node] = {"value": value, "note": note, "tone": tone}

    top_loss = lost["loss_reason"].value_counts(normalize=True)
    put("l2o_start", _num(len(lead)), "leads in 12 months")
    put("l2o_qualify", _pct((decided["status"] == "Qualified").mean()), "qualified, "
        + _dur(_median((decided["decided"] - decided["created"]).dt.total_seconds() / 3600)) + " to decide")
    put("l2o_nurture", _num((decided["status"] == "Disqualified").sum()), "disqualified")
    put("l2o_open", _num(len(opp)), "opportunities, repeat orders included")
    put("l2o_price", _dur(_median(opp["hours_to_first_quote"])), "median to the first quote", "hot")
    put("l2o_approve", _pct((quoted["first_quote_discount"] > 0.03).mean()), "of first quotes over 3%")
    put("l2o_send", _num(quoted["n_quotes"].sum()), "quotes, revisions included")
    put("l2o_expired", _pct((quoted["n_quotes"] > 1).mean()), "of quoted deals re-priced")
    put("l2o_accepts", _pct((closed["state"] == "Won").mean()), "of quoted deals won")
    put("l2o_declines", _num(len(lost)), "deals lost")
    put("l2o_loss", f"{top_loss.index[0]}" if len(top_loss) else "—",
        f"top reason, {_pct(top_loss.iloc[0]) if len(top_loss) else '—'} of losses")
    put("l2o_order", _num(lines["salesorderid"].nunique()), "orders, " + _irr(lines["net"].sum()))

    put("o2d_start", _num(dk["shipments"]), f"orders delivered, {_num(dk['tons'])} t")
    put("o2d_confirm", f"{dk['promise_days']:.0f} days", "median promised lead time")
    put("o2d_gw_stock", _pct(dk["from_stock"]), "from stock")
    put("o2d_reserve", _dur(dk["stock_ready_hours"]), "to reserve stock")
    put("o2d_buy", _pct(1 - dk["from_stock"]), "bought mill-direct")
    put("o2d_released", _dur(dk["mill_days"] * 24), "until the mill releases the load")
    put("o2d_truck", _dur(dk["yard_hours"]), "waiting in the yard for a truck", "hot")
    put("o2d_at_risk", _pct(1 - dk["on_time"]), "delivered late", "hot")
    put("o2d_load", f"{dk['weight_variance']:.2%}", "median weighbridge gap")
    put("o2d_gw_weight", _pct(dk["outside_tolerance"]), "of loads outside 0.5%",
        "hot" if dk["outside_tolerance"] > 0.08 else "")
    put("o2d_dispatch", _dur(dk["loading_hours"]), "loading to departure")
    put("o2d_deliver", _dur(dk["transit_hours"]), f"on the road; POD signed on {_pct(dk['pod'])}")
    put("o2d_invoice", _num(cash["invoices"]), "invoices")
    put("o2d_end", _pct(dk["otif"]), "on time and in full")

    put("i2c_send", _num(cash["invoices"]), "invoices, " + _irr(cash["amount"]))
    put("i2c_paid", _pct(cash["paid_on_time"]), "paid by the due date")
    put("i2c_due", _pct(cash["paid_late_or_overdue"]), "paid late or still overdue", "hot")
    put("i2c_call", _dur((cash["days_late_median"] or 0) * 24), "median delay when late")
    put("i2c_overdue", _pct(cash["over_30_late"]), "more than 30 days late")
    put("i2c_hold", _num(cash["accounts_over_30"]), "accounts over 30 days today")
    put("i2c_escalate", _irr(cash["amount_over_30"]), "open more than 30 days past due")
    put("i2c_match", _dur((cash["days_to_pay"] or 0) * 24), "median from invoice to payment")
    put("i2c_end", f"{ar['dso']:.0f} days", "days sales outstanding")

    n_req, h_req = cases("Mill certificate request", "Delivery change request")
    n_wl, h_wl = cases("Weight discrepancy", "Late delivery")
    n_q, h_q = cases("Quality / spec claim", "Damaged or rusted material")
    n_f, h_f = cases("Invoice dispute")
    top = svc["by_category"].head(2)
    put("c2r_start", _num(sk["cases"]), f"cases, {sk['per_100_deliveries']:.0f} per 100 deliveries")
    put("c2r_create", _pct(top.iloc[0]["share"]), f"{top.iloc[0]['category'].lower()}, the most common; "
        f"{top.iloc[1]['category'].lower()} {top.iloc[1]['share']:.0%}")
    put("c2r_respond", _pct(sk["response_sla"]), f"within SLA, median {_dur(sk['first_response_hours'])}")
    put("c2r_request", _num(n_req), f"requests, {_dur(h_req)} to close")
    put("c2r_check", _num(n_wl), f"weight and late claims, {_dur(h_wl)}")
    put("c2r_inspect", _num(n_q), f"quality and damage claims, {_dur(h_q)}", "hot")
    put("c2r_sla", _pct(sk["escalated"]), "of cases escalated")
    put("c2r_review", _num(n_f), f"invoice disputes, {_dur(h_f)}")
    put("c2r_gw_upheld", _pct(sk["upheld"]), "of claims upheld")
    put("c2r_credit", _irr(sk["compensation"]), "credit notes and replacements")
    put("c2r_resolve", _pct(sk["resolve_sla"]), "resolved within SLA", "hot" if sk["resolve_sla"] < 0.85 else "")
    put("c2r_survey", f"{sk['csat']:.1f} / 5", f"satisfaction, {_pct(sk['csat_responses'])} answered")
    return o
