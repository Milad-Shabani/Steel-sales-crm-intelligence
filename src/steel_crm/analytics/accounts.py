"""Customers and credit: who buys, who has gone quiet, and who owes money.

Steel is sold on credit to much of the market, so receivables are part of
the sales picture: a large order from a customer who pays 90 days late
ties up more cash than its margin is worth. Aging is measured on open
invoices at the export date; DSO is open receivables over the last 12
months of sales, times 365 (a 90-day window is too noisy for segments that
order a few times a year).

A customer is "at risk" when it has gone quiet for much longer than its
own habit: a distributor that orders every two weeks is worrying after a
month, a public project that orders twice a year is not.
"""

from __future__ import annotations

import pandas as pd

from ..warehouse.star_schema import Warehouse
from .sales import _in, windows

AGING_BUCKETS = [-10_000, 0, 30, 60, 90, 10_000]
AGING_LABELS = ["Not yet due", "1-30 days", "31-60 days", "61-90 days", "Over 90 days"]
AT_RISK_MIN_DAYS = 90
AT_RISK_GAP_MULTIPLE = 2.5


def receivables(wh: Warehouse) -> dict:
    inv = wh.fact_invoice
    open_ = inv[inv["is_open"]].copy()
    open_["days_overdue"] = (wh.as_of - open_["due_date"]).dt.days
    open_["bucket"] = pd.cut(open_["days_overdue"], AGING_BUCKETS, labels=AGING_LABELS, right=True)
    aging = open_.pivot_table(index="industry", columns="bucket", values="amount", aggfunc="sum",
                              observed=False, fill_value=0)
    aging = aging.reindex(columns=AGING_LABELS, fill_value=0)

    l12, _, end = windows(wh.as_of)
    lines = wh.fact_sales_line
    sales12 = lines[_in(lines["order_date"], l12, end)]
    rev12 = sales12.groupby("industry")["net"].sum()
    ar_ind = open_.groupby("industry")["amount"].sum()
    paid = inv[~inv["is_open"] & (inv["days_late"].notna())]
    paid12 = paid[paid["paid_on"] >= l12]
    by_industry = pd.DataFrame({
        "open_ar": ar_ind,
        "overdue": open_[open_["days_overdue"] > 0].groupby("industry")["amount"].sum(),
        "dso": ar_ind / rev12 * 365,
        "avg_days_late": paid12.groupby("industry")["days_late"].mean(),
    }).fillna({"overdue": 0}).sort_values("open_ar", ascending=False)
    by_industry["overdue_share"] = by_industry["overdue"] / by_industry["open_ar"]

    overdue = open_[open_["days_overdue"] > 0]
    top_overdue = overdue.groupby("accountid").agg(overdue=("amount", "sum"), invoices=("invoiceid", "count"),
                                                   oldest_days=("days_overdue", "max"))
    top_overdue = top_overdue.join(wh.dim_account.set_index("accountid")[["name", "industry", "owner"]])
    total_ar = float(open_["amount"].sum())
    return {
        "aging": aging.reset_index(),
        "by_industry": by_industry.reset_index(),
        "top_overdue": top_overdue.sort_values("overdue", ascending=False).head(8).reset_index(),
        "open_ar": total_ar,
        "overdue": float(overdue["amount"].sum()),
        "overdue_share": float(overdue["amount"].sum() / total_ar) if total_ar else 0.0,
        "dso": float(total_ar / sales12["net"].sum() * 365) if len(sales12) else None,
    }


def top_accounts(wh: Warehouse, n: int = 10) -> pd.DataFrame:
    l12, p12, end = windows(wh.as_of)
    lines = wh.fact_sales_line
    cur = lines[_in(lines["order_date"], l12, end)].groupby("accountid").agg(
        revenue=("net", "sum"), tons=("tons", "sum"), orders=("salesorderid", "nunique"),
        margin=("margin", "sum"))
    prev = lines[_in(lines["order_date"], p12, l12)].groupby("accountid")["net"].sum()
    last = lines.groupby("accountid")["order_date"].max()
    acc = wh.dim_account.set_index("accountid")
    out = cur.join(acc[["name", "industry", "province", "owner"]])
    out["growth"] = out["revenue"] / prev.reindex(out.index) - 1
    out["last_order"] = last.reindex(out.index)
    return out.sort_values("revenue", ascending=False).head(n).reset_index()


def at_risk(wh: Warehouse, n: int = 10) -> tuple[pd.DataFrame, dict]:
    """Regular customers (3+ orders) silent for over 2.5x their usual reorder gap."""
    lines = wh.fact_sales_line
    orders = lines.groupby(["accountid", "salesorderid"])["order_date"].first().reset_index()
    orders = orders.sort_values(["accountid", "order_date"])
    orders["gap"] = orders.groupby("accountid")["order_date"].diff().dt.days
    g = lines.groupby("accountid").agg(orders=("salesorderid", "nunique"), revenue=("net", "sum"),
                                       tons=("tons", "sum"), last_order=("order_date", "max"))
    g["usual_gap"] = orders.groupby("accountid")["gap"].median()
    g["days_silent"] = (wh.as_of - g["last_order"]).dt.days
    limit = (g["usual_gap"] * AT_RISK_GAP_MULTIPLE).clip(lower=AT_RISK_MIN_DAYS)
    risk = g[(g["orders"] >= 3) & (g["days_silent"] > limit)]
    l12, p12, _ = windows(wh.as_of)
    earlier = lines[_in(lines["order_date"], p12, l12)].groupby("accountid")["net"].sum()
    risk = risk.join(wh.dim_account.set_index("accountid")[["name", "industry", "province", "owner"]])
    risk["revenue_prior_year"] = earlier.reindex(risk.index).fillna(0)
    summary = {"accounts": int(len(risk)), "revenue_prior_year": float(risk["revenue_prior_year"].sum()),
               "regular_customers": int((g["orders"] >= 3).sum())}
    return risk.sort_values("revenue", ascending=False).head(n).reset_index(), summary
