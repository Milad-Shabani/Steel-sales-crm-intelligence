"""Sales performance: revenue and tons, price realization and margin, why
deals are won and lost, the sales team, and where the business comes from.

"Last 12 months" (L12) always means the 365 days up to the export date, and
is compared with the 365 days before that (P12).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..warehouse.star_schema import Warehouse

QUOTE_BUCKETS = [0, 4, 24, 72, np.inf]
QUOTE_LABELS = ["Under 4 hours", "4-24 hours", "1-3 days", "Over 3 days"]
DISCOUNT_BUCKETS = [-0.001, 0.01, 0.02, 0.03, 0.04, 1]
DISCOUNT_LABELS = ["0-1%", "1-2%", "2-3%", "3-4%", "4%+"]


def windows(as_of: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """Start of L12, start of P12, and the exclusive end of L12."""
    end = as_of + pd.Timedelta(days=1)
    return end - pd.Timedelta(days=365), end - pd.Timedelta(days=730), end


def _in(series: pd.Series, start, end) -> pd.Series:
    return (series >= start) & (series < end)


def _pct_change(new, old):
    return None if not old else float(new / old - 1)


def kpis(wh: Warehouse, scored: pd.DataFrame, ar: dict, roi: pd.DataFrame) -> dict:
    l12, p12, end = windows(wh.as_of)
    lines = wh.fact_sales_line
    cur, prev = lines[_in(lines["order_date"], l12, end)], lines[_in(lines["order_date"], p12, l12)]
    fo = scored
    closed = fo[fo["won"].notna()]
    c_cur = closed[_in(closed["close_date"], l12, end)]
    c_prev = closed[_in(closed["close_date"], p12, l12)]
    quoted = fo[fo["first_quote_at"].notna() & _in(fo["created"], l12, end)]
    open_ = fo[fo["state"] == "Open"]
    mpt = cur["margin"].sum() / cur["tons"].sum()
    mpt_prev = prev["margin"].sum() / prev["tons"].sum() if len(prev) else None
    spend = roi["cost"].sum()
    return {
        "revenue": float(cur["net"].sum()), "revenue_delta": _pct_change(cur["net"].sum(), prev["net"].sum()),
        "tons": float(cur["tons"].sum()), "tons_delta": _pct_change(cur["tons"].sum(), prev["tons"].sum()),
        "orders": int(cur["salesorderid"].nunique()),
        "margin_per_ton": float(mpt), "margin_per_ton_delta": _pct_change(mpt, mpt_prev),
        "margin_pct": float(cur["margin"].sum() / cur["net"].sum()),
        "win_rate": float(c_cur["won"].mean()),
        "win_rate_delta_pp": float(c_cur["won"].mean() - c_prev["won"].mean()) if len(c_prev) else None,
        "new_business_win_rate": float(c_cur[~c_cur["is_repeat"]]["won"].mean()),
        "avg_discount": float(cur["discount"].sum() / cur["gross"].sum()),
        "median_hours_to_quote": float(quoted["hours_to_first_quote"].median()),
        "quoted_within_24h": float((quoted["hours_to_first_quote"] < 24).mean()),
        "open_deals": int(len(open_)), "open_value": float(open_["estimated_value"].sum()),
        "open_crm_weighted": float((open_["estimated_value"] * open_["crm_probability"]).sum()),
        "open_model_weighted": float((open_["estimated_value"] * open_["p_model"]).sum()),
        "marketing_spend": float(spend),
        "marketing_margin": float(roi["acquired_margin"].sum()),
        "marketing_roi": float((roi["acquired_margin"].sum() - spend) / spend),
        "dso": ar["dso"], "overdue": ar["overdue"], "overdue_share": ar["overdue_share"], "open_ar": ar["open_ar"],
        "active_customers": int(cur["accountid"].nunique()),
    }


def monthly(wh: Warehouse) -> pd.DataFrame:
    lines = wh.fact_sales_line
    m = lines.groupby("month").agg(revenue=("net", "sum"), tons=("tons", "sum"), gross=("gross", "sum"),
                                   discount=("discount", "sum"), cost=("cost", "sum"), margin=("margin", "sum"),
                                   orders=("salesorderid", "nunique"))
    m = m.join(wh.price_index.set_index("month"), how="outer")
    m["list_per_ton"] = m["gross"] / m["tons"]
    m["realized_per_ton"] = m["revenue"] / m["tons"]
    m["cost_per_ton"] = m["cost"] / m["tons"]
    m["margin_per_ton"] = m["margin"] / m["tons"]
    m["discount_pct"] = m["discount"] / m["gross"]
    leads = wh.fact_lead.groupby(wh.fact_lead["created"].dt.to_period("M")).size()
    m["new_leads"] = leads.reindex(m.index).fillna(0).astype(int)
    m.index = m.index.astype(str)
    return m.reset_index().rename(columns={"index": "month"})


def product_groups(wh: Warehouse) -> pd.DataFrame:
    l12, _, end = windows(wh.as_of)
    lines = wh.fact_sales_line[_in(wh.fact_sales_line["order_date"], l12, end)]
    g = lines.groupby("product_group").agg(revenue=("net", "sum"), tons=("tons", "sum"), margin=("margin", "sum"),
                                           gross=("gross", "sum"), discount=("discount", "sum"))
    g["margin_per_ton"] = g["margin"] / g["tons"]
    g["margin_pct"] = g["margin"] / g["revenue"]
    g["discount_pct"] = g["discount"] / g["gross"]
    closed = wh.fact_opportunity[wh.fact_opportunity["won"].notna()]
    g["win_rate"] = closed.groupby("product_group")["won"].mean()
    return g.reset_index().sort_values("revenue", ascending=False)


def win_analysis(fo: pd.DataFrame) -> dict[str, pd.DataFrame]:
    closed = fo[fo["won"].notna()].copy()
    quoted = closed[closed["first_quote_at"].notna()].copy()
    quoted["bucket"] = pd.cut(quoted["hours_to_first_quote"], QUOTE_BUCKETS, labels=QUOTE_LABELS, right=False)
    speed = quoted.groupby("bucket", observed=False).agg(deals=("won", "size"), win_rate=("won", "mean"))
    never = closed[closed["first_quote_at"].isna()]
    speed.loc["Never quoted"] = [len(never), never["won"].mean() if len(never) else np.nan]

    quoted["disc_bucket"] = pd.cut(quoted["first_quote_discount"], DISCOUNT_BUCKETS, labels=DISCOUNT_LABELS)
    disc = quoted.groupby("disc_bucket", observed=False).agg(deals=("won", "size"), win_rate=("won", "mean"))

    industry = closed.groupby("industry").agg(deals=("won", "size"), win_rate=("won", "mean"),
                                              cycle_days=("cycle_days", "median"))
    channel = closed.groupby("channel").agg(deals=("won", "size"), win_rate=("won", "mean"))
    competitor = closed.assign(c=closed["competitor"].fillna("No competitor")).groupby("c").agg(
        deals=("won", "size"), win_rate=("won", "mean"))
    lost = closed[closed["won"] == 0]
    reasons = lost.groupby("loss_reason").agg(deals=("opportunityid", "count"),
                                              lost_value=("estimated_value", "sum"))
    reasons["share"] = reasons["deals"] / reasons["deals"].sum()
    return {
        "quote_speed": speed.reset_index(names="bucket"),
        "discount": disc.reset_index(names="bucket"),
        "industry": industry.reset_index().sort_values("win_rate", ascending=False),
        "channel": channel.reset_index().sort_values("win_rate", ascending=False),
        "competitor": competitor.reset_index(names="competitor").sort_values("deals", ascending=False),
        "loss_reasons": reasons.reset_index().sort_values("deals", ascending=False),
    }


def discount_margin(wh: Warehouse) -> pd.DataFrame:
    """Margin per ton on won orders, by the discount given."""
    lines = wh.fact_sales_line.copy()
    lines["disc"] = lines["discount"] / lines["gross"]
    lines["bucket"] = pd.cut(lines["disc"], DISCOUNT_BUCKETS, labels=DISCOUNT_LABELS)
    g = lines.groupby("bucket", observed=False).agg(tons=("tons", "sum"), margin=("margin", "sum"))
    g["margin_per_ton"] = g["margin"] / g["tons"]
    return g.reset_index()


def reps(wh: Warehouse, scored: pd.DataFrame) -> pd.DataFrame:
    l12, _, end = windows(wh.as_of)
    lines = wh.fact_sales_line[_in(wh.fact_sales_line["order_date"], l12, end)]
    fo = scored
    closed = fo[fo["won"].notna() & _in(fo["close_date"], l12, end)]
    created = fo[_in(fo["created"], l12, end)]
    open_ = fo[fo["state"] == "Open"]
    users = wh.dim_user.set_index("systemuserid")
    sales = lines.groupby("ownerid").agg(revenue=("net", "sum"), tons=("tons", "sum"), margin=("margin", "sum"),
                                         gross=("gross", "sum"), discount=("discount", "sum"))
    out = pd.DataFrame({
        "rep": users["fullname"], "territory": users["territory"],
    }).join(sales, how="inner")
    out["margin_per_ton"] = out["margin"] / out["tons"]
    out["avg_discount"] = out["discount"] / out["gross"]
    out["deals_won"] = closed[closed["won"] == 1].groupby("ownerid").size()
    out["win_rate"] = closed.groupby("ownerid")["won"].mean()
    out["median_hours_to_quote"] = created.groupby("ownerid")["hours_to_first_quote"].median()
    out["activities_per_deal"] = created.groupby("ownerid")["activities"].mean()
    out["open_deals"] = open_.groupby("ownerid").size()
    out["open_crm_weighted"] = (open_["estimated_value"] * open_["crm_probability"]).groupby(open_["ownerid"]).sum()
    out["open_model_weighted"] = (open_["estimated_value"] * open_["p_model"]).groupby(open_["ownerid"]).sum()
    test = fo[fo["p_model"].notna() & fo["won"].notna()]
    out["crm_optimism"] = (test["rep_prob_last"] - test["won"]).groupby(test["ownerid"]).mean()
    out = out.fillna({"deals_won": 0, "open_deals": 0, "open_crm_weighted": 0, "open_model_weighted": 0})
    return out.reset_index(drop=True).sort_values("revenue", ascending=False)


def provinces(wh: Warehouse) -> pd.DataFrame:
    l12, _, end = windows(wh.as_of)
    lines = wh.fact_sales_line[_in(wh.fact_sales_line["order_date"], l12, end)]
    g = lines.groupby("province").agg(revenue=("net", "sum"), tons=("tons", "sum"), margin=("margin", "sum"),
                                      customers=("accountid", "nunique"))
    g["margin_per_ton"] = g["margin"] / g["tons"]
    return g.reset_index().sort_values("revenue", ascending=False)


def pipeline(scored: pd.DataFrame) -> dict[str, pd.DataFrame]:
    open_ = scored[scored["state"] == "Open"].copy()
    open_["crm_weighted"] = open_["estimated_value"] * open_["crm_probability"]
    open_["model_weighted"] = open_["estimated_value"] * open_["p_model"]
    by_stage = open_.groupby("stage").agg(deals=("opportunityid", "count"), value=("estimated_value", "sum"),
                                          crm_weighted=("crm_weighted", "sum"),
                                          model_weighted=("model_weighted", "sum"))
    open_["close_month"] = open_["estimated_close"].dt.to_period("M").astype(str)
    by_month = open_.groupby("close_month").agg(deals=("opportunityid", "count"), value=("estimated_value", "sum"),
                                                crm_weighted=("crm_weighted", "sum"),
                                                model_weighted=("model_weighted", "sum"))
    top = open_.sort_values("model_weighted", ascending=False).head(10)
    return {"by_stage": by_stage.reset_index(), "by_month": by_month.reset_index(), "top": top}
