"""Model the decoded Dataverse tables as a star schema.

Dimensions: date, account, product, sales rep, campaign. Facts: leads,
opportunities (one row per deal, with the features the win model uses),
order lines (tons, list price, discount, cost and margin), invoices
(with days late), campaign responses, activities and weekly pipeline
snapshots. The same tables are written to SQLite for the analytics and as
CSV files that load straight into Power BI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..ingest.dataverse import DataverseExport

REPEAT_CHANNEL = "Repeat business"
LEGACY_CHANNEL = "Customer before 2024-07"


@dataclass
class Warehouse:
    dim_date: pd.DataFrame
    dim_account: pd.DataFrame
    dim_product: pd.DataFrame
    dim_user: pd.DataFrame
    dim_campaign: pd.DataFrame
    fact_lead: pd.DataFrame
    fact_opportunity: pd.DataFrame
    fact_sales_line: pd.DataFrame
    fact_invoice: pd.DataFrame
    fact_campaign_response: pd.DataFrame
    fact_activity: pd.DataFrame
    fact_pipeline_snapshot: pd.DataFrame
    price_index: pd.DataFrame
    as_of: pd.Timestamp

    def tables(self) -> dict[str, pd.DataFrame]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, pd.DataFrame)}


def _price_index(t) -> pd.DataFrame:
    ppl = t["productpricelevel"].merge(t["pricelevel"][["pricelevelid", "begindate"]], on="pricelevelid")
    ppl["month"] = ppl["begindate"].dt.to_period("M")
    first = ppl.sort_values("month").groupby("productid")["amount"].first()
    ppl["rel"] = ppl["amount"] / ppl["productid"].map(first)
    idx = ppl.groupby("month", as_index=False).agg(price_index=("rel", "mean"), avg_list_price=("amount", "mean"))
    idx["price_index"] = (idx["price_index"] * 100).round(1)
    return idx


def build_warehouse(export: DataverseExport) -> Warehouse:
    t = export.tables
    as_of = max(t["opportunity"]["createdon"].max(), t["activitypointer"]["createdon"].max()).normalize()

    territory = dict(zip(t["territory"]["territoryid"], t["territory"]["name"]))
    dim_user = t["systemuser"][["systemuserid", "fullname", "title", "territoryid"]].copy()
    dim_user["territory"] = dim_user["territoryid"].map(territory)
    rep_name = dict(zip(dim_user["systemuserid"], dim_user["fullname"]))

    camp = t["campaign"]
    dim_campaign = camp[["campaignid", "name", "codename", "ahn_channel_label", "typecode_label",
                         "actualstart", "actualend", "budgetedcost", "totalactualcost"]]
    dim_campaign = dim_campaign.rename(columns={
        "ahn_channel_label": "channel", "typecode_label": "campaign_type", "actualstart": "start",
        "actualend": "end", "totalactualcost": "cost"})
    camp_channel = dict(zip(dim_campaign["campaignid"], dim_campaign["channel"]))

    # leads and their channel (campaign channel, else the lead source)
    lead = t["lead"]
    fact_lead = pd.DataFrame({
        "leadid": lead["leadid"], "created": lead["createdon"],
        "decided": lead["modifiedon"].where(lead["statecode"] > 0),
        "status": lead["statecode_label"], "status_reason": lead["statuscode_label"],
        "source": lead["leadsourcecode_label"], "campaignid": lead["campaignid"],
        "channel": lead["campaignid"].map(camp_channel).fillna(lead["leadsourcecode_label"]),
        "industry": lead["industrycode_label"], "province": lead["address1_stateorprovince"],
        "product_group": lead["ahn_productgroup_label"], "estimated_tons": lead["ahn_estimatedtonnage"],
        "ownerid": lead["ownerid"], "accountid": lead["parentaccountid"],
        "opportunityid": lead["qualifyingopportunityid"],
    })
    lead_channel = dict(zip(fact_lead["leadid"], fact_lead["channel"]))

    acc = t["account"]
    dim_account = pd.DataFrame({
        "accountid": acc["accountid"], "name": acc["name"], "accountnumber": acc["accountnumber"],
        "industry": acc["industrycode_label"], "province": acc["address1_stateorprovince"],
        "territory": acc["territoryid"].map(territory), "ownerid": acc["ownerid"],
        "owner": acc["ownerid"].map(rep_name), "payment_terms": acc["paymenttermscode_label"],
        "credit_limit": acc["creditlimit"], "created": acc["createdon"],
        "customer_type": acc["customertypecode_label"],
        "acquisition_channel": acc["originatingleadid"].map(lead_channel).fillna(LEGACY_CHANNEL),
        "originatingleadid": acc["originatingleadid"],
    })
    acc_info = dim_account.set_index("accountid")

    prod = t["product"]
    dim_product = prod[["productid", "productnumber", "name", "ahn_productgroup_label", "ahn_grade"]].rename(
        columns={"ahn_productgroup_label": "product_group", "ahn_grade": "grade"})

    # ---------------------------------------------------------------- opportunities
    opp = t["opportunity"].copy()
    quotes = t["quote"].sort_values(["opportunityid", "createdon"])
    q_first = quotes.groupby("opportunityid").agg(
        first_quote_at=("createdon", "first"), first_quote_discount=("discountpercentage", "first"),
        last_quote_discount=("discountpercentage", "last"), n_quotes=("quoteid", "count"))
    acts = t["activitypointer"]
    opp_acts = acts[acts["regardingobjecttypecode"] == "opportunity"].merge(
        opp[["opportunityid", "createdon"]], left_on="regardingobjectid", right_on="opportunityid",
        suffixes=("", "_opp"))
    early = opp_acts[opp_acts["createdon"] <= opp_acts["createdon_opp"] + pd.Timedelta(days=3)]
    n_early = early.groupby("opportunityid").size()
    n_all = opp_acts.groupby("opportunityid").size()
    comp = t["opportunitycompetitors"].merge(t["competitor"][["competitorid", "name"]], on="competitorid")
    competitor = comp.groupby("opportunityid")["name"].first()

    snaps = t["ahn_pipelinesnapshot"].sort_values("ahn_snapshotdate")
    snaps = snaps.merge(opp[["opportunityid", "actualclosedate"]], left_on="ahn_opportunityid",
                        right_on="opportunityid", how="left")
    before_close = snaps[snaps["actualclosedate"].isna() | (snaps["ahn_snapshotdate"] < snaps["actualclosedate"])]
    rep_prob_last = before_close.groupby("ahn_opportunityid")["ahn_closeprobability"].last()

    idx = _price_index(t).set_index("month")["price_index"]
    month = opp["createdon"].dt.to_period("M")
    trailing = (idx.reindex(month).to_numpy() / idx.reindex(month - 1).to_numpy()) - 1

    state = opp["statecode_label"]
    fact_opportunity = pd.DataFrame({
        "opportunityid": opp["opportunityid"], "accountid": opp["customerid"], "ownerid": opp["ownerid"],
        "owner": opp["ownerid"].map(rep_name), "originatingleadid": opp["originatingleadid"],
        "campaignid": opp["campaignid"],
        "channel": np.where(opp["ahn_isrepeatbusiness"].astype(bool), REPEAT_CHANNEL,
                            opp["originatingleadid"].map(lead_channel)),
        "industry": opp["customerid"].map(acc_info["industry"]),
        "province": opp["customerid"].map(acc_info["province"]),
        "territory": opp["customerid"].map(acc_info["territory"]),
        "product_group": opp["ahn_productgroup_label"], "tons": opp["ahn_tonnage"],
        "is_repeat": opp["ahn_isrepeatbusiness"].astype(bool), "created": opp["createdon"],
        "close_date": opp["actualclosedate"], "estimated_close": opp["estimatedclosedate"],
        "state": state, "won": np.where(state == "Won", 1.0, np.where(state == "Lost", 0.0, np.nan)),
        "status_reason": opp["statuscode_label"], "loss_reason": opp["ahn_lossreason_label"],
        "stage": opp["stepname"], "forecast_category": opp["msdyn_forecastcategory_label"],
        "estimated_value": opp["estimatedvalue"], "actual_value": opp["actualvalue"],
        "crm_probability": opp["closeprobability"] / 100,
        "rep_prob_last": opp["opportunityid"].map(rep_prob_last) / 100,
        "price_trend": trailing,
    })
    fq = q_first.reindex(fact_opportunity["opportunityid"])
    fact_opportunity["first_quote_at"] = fq["first_quote_at"].to_numpy()
    fact_opportunity["hours_to_first_quote"] = (
        (fact_opportunity["first_quote_at"] - fact_opportunity["created"]).dt.total_seconds() / 3600).round(1)
    fact_opportunity["first_quote_discount"] = fq["first_quote_discount"].to_numpy() / 100
    fact_opportunity["last_quote_discount"] = fq["last_quote_discount"].to_numpy() / 100
    fact_opportunity["n_quotes"] = fq["n_quotes"].fillna(0).astype(int).to_numpy()
    fact_opportunity["activities_first_3d"] = fact_opportunity["opportunityid"].map(n_early).fillna(0).astype(int)
    fact_opportunity["activities"] = fact_opportunity["opportunityid"].map(n_all).fillna(0).astype(int)
    fact_opportunity["competitor"] = fact_opportunity["opportunityid"].map(competitor)
    created_day = fact_opportunity["created"].dt.normalize()
    fact_opportunity["cycle_days"] = (fact_opportunity["close_date"] - created_day).dt.days

    # ---------------------------------------------------------------- order lines
    so = t["salesorder"]
    lines = t["salesorderdetail"].merge(
        so[["salesorderid", "opportunityid", "customerid", "ownerid", "submitdate"]], on="salesorderid")
    lines = lines.merge(dim_product[["productid", "product_group", "name"]], on="productid")
    fact_sales_line = pd.DataFrame({
        "salesorderdetailid": lines["salesorderdetailid"], "salesorderid": lines["salesorderid"],
        "opportunityid": lines["opportunityid"], "accountid": lines["customerid"], "ownerid": lines["ownerid"],
        "productid": lines["productid"], "product": lines["name"], "product_group": lines["product_group"],
        "order_date": lines["submitdate"], "tons": lines["quantity"], "list_price": lines["priceperunit"],
        "gross": lines["baseamount"], "discount": lines["manualdiscountamount"], "net": lines["extendedamount"],
        "cost": lines["quantity"] * lines["ahn_costperton"],
    })
    fact_sales_line["margin"] = fact_sales_line["net"] - fact_sales_line["cost"]
    fact_sales_line["month"] = fact_sales_line["order_date"].dt.to_period("M")
    fact_sales_line["industry"] = fact_sales_line["accountid"].map(acc_info["industry"])
    fact_sales_line["province"] = fact_sales_line["accountid"].map(acc_info["province"])

    # ---------------------------------------------------------------- invoices
    inv = t["invoice"]
    paid_on = inv["ahn_paidon"]
    fact_invoice = pd.DataFrame({
        "invoiceid": inv["invoiceid"], "salesorderid": inv["salesorderid"], "accountid": inv["customerid"],
        "ownerid": inv["ownerid"], "invoice_date": inv["createdon"], "due_date": inv["duedate"],
        "paid_on": paid_on, "amount": inv["totalamount"], "payment_terms": inv["paymenttermscode_label"],
        "is_open": paid_on.isna(),
    })
    fact_invoice["days_to_pay"] = (paid_on - inv["createdon"]).dt.days
    fact_invoice["days_late"] = (paid_on.dt.normalize() - inv["duedate"]).dt.days
    fact_invoice["industry"] = fact_invoice["accountid"].map(acc_info["industry"])

    resp = t["campaignresponse"]
    fact_campaign_response = pd.DataFrame({
        "activityid": resp["activityid"], "campaignid": resp["regardingobjectid"],
        "received": resp["receivedon"], "response": resp["responsecode_label"],
        "channel": resp["regardingobjectid"].map(camp_channel)})
    fact_activity = acts[["activityid", "activitytypecode", "regardingobjecttypecode", "regardingobjectid",
                          "ownerid", "createdon"]].rename(columns={"createdon": "created"})
    fact_pipeline_snapshot = t["ahn_pipelinesnapshot"][[
        "ahn_snapshotdate", "ahn_opportunityid", "ownerid", "ahn_stepname", "ahn_closeprobability",
        "ahn_estimatedvalue", "ahn_forecastcategory_label"]].rename(columns={
            "ahn_snapshotdate": "snapshot_date", "ahn_opportunityid": "opportunityid", "ahn_stepname": "stage",
            "ahn_closeprobability": "close_probability", "ahn_estimatedvalue": "estimated_value",
            "ahn_forecastcategory_label": "forecast_category"})

    days = pd.date_range(min(dim_account["created"].min(), as_of - pd.Timedelta(days=800)).normalize(), as_of)
    dim_date = pd.DataFrame({"date": days, "year": days.year, "quarter": days.quarter, "month": days.month,
                             "month_name": days.strftime("%b"), "weekday": days.strftime("%a")})

    return Warehouse(dim_date, dim_account, dim_product, dim_user, dim_campaign, fact_lead, fact_opportunity,
                     fact_sales_line, fact_invoice, fact_campaign_response, fact_activity,
                     fact_pipeline_snapshot, _price_index(t), as_of)


def write_warehouse(wh: Warehouse, db_path: Path, csv_dir: Path | None = None) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        for name, df in wh.tables().items():
            out = df.copy()
            for col in out.columns:
                if isinstance(out[col].dtype, pd.PeriodDtype):
                    out[col] = out[col].astype(str)
            out.to_sql(name, conn, index=False)
            if csv_dir is not None:
                csv_dir.mkdir(parents=True, exist_ok=True)
                out.to_csv(csv_dir / f"{name}.csv", index=False)
