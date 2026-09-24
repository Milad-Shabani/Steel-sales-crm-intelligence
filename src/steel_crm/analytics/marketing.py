"""Marketing: lead quality by source and what each campaign really returned.

Attribution is first-touch through the lead: a deal belongs to the campaign
of the lead it was qualified from. Two returns are measured against what a
campaign cost. First-deal ROI uses the gross margin of the deals its leads
closed. Customer-lifetime ROI uses the gross margin of every order, repeat
business included, from the accounts the campaign brought in, over the
whole horizon: in a business built on reorders, a campaign that loses
money on the first deal can still be worth running.
"""

from __future__ import annotations

import pandas as pd

from ..warehouse.star_schema import Warehouse


def lead_sources(wh: Warehouse) -> pd.DataFrame:
    leads = wh.fact_lead.copy()
    opps = wh.fact_opportunity.set_index("opportunityid")
    leads["won"] = leads["opportunityid"].map(opps["state"]).eq("Won")
    decided = leads[leads["status"] != "Open"]
    out = decided.groupby("channel").agg(
        leads=("leadid", "count"), qualified=("status", lambda s: (s == "Qualified").sum()),
        won=("won", "sum"), avg_tons=("estimated_tons", "mean"))
    out["qualify_rate"] = out["qualified"] / out["leads"]
    out["lead_to_win"] = out["won"] / out["leads"]
    return out.reset_index().sort_values("leads", ascending=False)


def campaign_roi(wh: Warehouse) -> tuple[pd.DataFrame, pd.DataFrame]:
    leads = wh.fact_lead[wh.fact_lead["campaignid"].notna()]
    lead_campaign = dict(zip(leads["leadid"], leads["campaignid"]))
    opps = wh.fact_opportunity
    lines = wh.fact_sales_line

    first = opps[opps["originatingleadid"].isin(lead_campaign)].copy()
    first["campaignid"] = first["originatingleadid"].map(lead_campaign)
    first_won = first[first["state"] == "Won"]
    first_lines = lines[lines["opportunityid"].isin(first_won["opportunityid"])].merge(
        first_won[["opportunityid", "campaignid"]], on="opportunityid")
    first_rev = first_lines.groupby("campaignid")["net"].sum()
    first_margin = first_lines.groupby("campaignid")["margin"].sum()

    acquired = wh.dim_account[wh.dim_account["originatingleadid"].isin(lead_campaign)].copy()
    acquired["campaignid"] = acquired["originatingleadid"].map(lead_campaign)
    acq_lines = lines.merge(acquired[["accountid", "campaignid"]], on="accountid")
    acq = acq_lines.groupby("campaignid").agg(acquired_revenue=("net", "sum"), acquired_margin=("margin", "sum"),
                                              acquired_tons=("tons", "sum"))
    responses = wh.fact_campaign_response.groupby("campaignid").size()

    c = wh.dim_campaign.set_index("campaignid")
    out = pd.DataFrame({
        "name": c["name"], "channel": c["channel"], "start": c["start"], "cost": c["cost"],
        "responses": responses.reindex(c.index).fillna(0).astype(int),
        "leads": leads.groupby("campaignid").size().reindex(c.index).fillna(0).astype(int),
        "qualified": leads[leads["status"] == "Qualified"].groupby("campaignid").size()
        .reindex(c.index).fillna(0).astype(int),
        "won_deals": first_won.groupby("campaignid").size().reindex(c.index).fillna(0).astype(int),
        "first_deal_revenue": first_rev.reindex(c.index).fillna(0),
        "first_deal_margin": first_margin.reindex(c.index).fillna(0),
        "acquired_accounts": acquired.groupby("campaignid").size().reindex(c.index).fillna(0).astype(int),
    }).join(acq).fillna({"acquired_revenue": 0, "acquired_margin": 0, "acquired_tons": 0})
    out = _ratios(out)

    by_channel = out.groupby("channel").agg(
        campaigns=("name", "count"), cost=("cost", "sum"), responses=("responses", "sum"),
        leads=("leads", "sum"), qualified=("qualified", "sum"), won_deals=("won_deals", "sum"),
        first_deal_revenue=("first_deal_revenue", "sum"), first_deal_margin=("first_deal_margin", "sum"),
        acquired_accounts=("acquired_accounts", "sum"),
        acquired_revenue=("acquired_revenue", "sum"), acquired_margin=("acquired_margin", "sum"),
        acquired_tons=("acquired_tons", "sum"))
    by_channel = _ratios(by_channel).sort_values("roi", ascending=False)
    return out.reset_index().sort_values("roi", ascending=False), by_channel.reset_index()


def _ratios(df: pd.DataFrame) -> pd.DataFrame:
    df["cost_per_lead"] = df["cost"] / df["leads"].where(df["leads"] > 0)
    df["cost_per_won_deal"] = df["cost"] / df["won_deals"].where(df["won_deals"] > 0)
    df["first_deal_roi"] = (df["first_deal_margin"] - df["cost"]) / df["cost"]
    df["roi"] = (df["acquired_margin"] - df["cost"]) / df["cost"]
    return df
