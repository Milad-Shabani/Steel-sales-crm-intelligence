"""Data-quality checks on the Dataverse export, run before anything is modelled.

CRM data is typed in by people, so the checks are the kind a CRM admin
cares about: keys that are unique, lookups that point at real records,
option-set codes that have a label, and business rules that the sales
process implies (a won deal has a close date, a value and a sales order;
a qualified lead points at the opportunity it became; an order line's
amount adds up). Errors stop the pipeline; warnings are reported.
"""

from __future__ import annotations

import pandas as pd

from ..ingest.dataverse import DataverseExport

PRIMARY_KEYS = {
    "systemuser": "systemuserid", "territory": "territoryid", "product": "productid",
    "pricelevel": "pricelevelid", "competitor": "competitorid", "account": "accountid",
    "campaign": "campaignid", "campaignresponse": "activityid", "lead": "leadid",
    "opportunity": "opportunityid", "quote": "quoteid", "salesorder": "salesorderid",
    "salesorderdetail": "salesorderdetailid", "invoice": "invoiceid", "activitypointer": "activityid",
}

# child table, lookup column, parent table, may be empty
FOREIGN_KEYS = [
    ("systemuser", "territoryid", "territory", True),
    ("account", "ownerid", "systemuser", False),
    ("account", "territoryid", "territory", False),
    ("account", "originatingleadid", "lead", True),
    ("campaignresponse", "regardingobjectid", "campaign", False),
    ("lead", "campaignid", "campaign", True),
    ("lead", "relatedobjectid", "campaignresponse", True),
    ("lead", "parentaccountid", "account", True),
    ("lead", "qualifyingopportunityid", "opportunity", True),
    ("lead", "ownerid", "systemuser", False),
    ("opportunity", "customerid", "account", False),
    ("opportunity", "originatingleadid", "lead", True),
    ("opportunity", "campaignid", "campaign", True),
    ("opportunity", "ownerid", "systemuser", False),
    ("opportunitycompetitors", "opportunityid", "opportunity", False),
    ("opportunitycompetitors", "competitorid", "competitor", False),
    ("quote", "opportunityid", "opportunity", False),
    ("salesorder", "opportunityid", "opportunity", False),
    ("salesorder", "customerid", "account", False),
    ("salesorderdetail", "salesorderid", "salesorder", False),
    ("salesorderdetail", "productid", "product", False),
    ("productpricelevel", "productid", "product", False),
    ("productpricelevel", "pricelevelid", "pricelevel", False),
    ("invoice", "salesorderid", "salesorder", False),
    ("invoice", "customerid", "account", False),
    ("ahn_pipelinesnapshot", "ahn_opportunityid", "opportunity", False),
]

REGARDING = {"lead": "lead", "opportunity": "opportunity", "account": "account"}


def _row(check, entity, failed, total, severity="error", detail=""):
    return dict(check=check, entity=entity, failed=int(failed), total=int(total), severity=severity,
                status="pass" if failed == 0 else ("fail" if severity == "error" else "warn"),
                detail=detail)


def run_checks(export: DataverseExport) -> pd.DataFrame:
    t = export.tables
    out = []

    for entity, pk in PRIMARY_KEYS.items():
        df = t[entity]
        bad = df[pk].isna() | df[pk].duplicated(keep=False)
        out.append(_row("primary key unique and present", entity, bad.sum(), len(df)))

    for child, col, parent, nullable in FOREIGN_KEYS:
        df = t[child]
        if col not in df.columns:
            continue
        values = df[col]
        known = set(t[parent][PRIMARY_KEYS[parent]])
        present = values.notna()
        orphans = present & ~values.isin(known)
        empty = (~present).sum() if not nullable else 0
        out.append(_row(f"{col} -> {parent}", child, orphans.sum() + empty, len(df)))

    acts = t["activitypointer"]
    for rtype, parent in REGARDING.items():
        sub = acts[acts["regardingobjecttypecode"] == rtype]
        orphans = ~sub["regardingobjectid"].isin(set(t[parent][PRIMARY_KEYS[parent]]))
        out.append(_row(f"regardingobjectid -> {parent}", "activitypointer", orphans.sum(), len(sub)))

    for item in export.unmapped_codes:
        out.append(_row(f"{item['column']} codes have a label", item["entity"], item["rows"], len(t[item["entity"]]),
                        "warning", f"codes without a label: {item['codes']}"))
    if not export.unmapped_codes:
        out.append(_row("option-set codes have a label", "all", 0, sum(len(d) for d in t.values())))

    opp = t["opportunity"]
    won, lost = opp[opp["statecode"] == 1], opp[opp["statecode"] == 2]
    out.append(_row("won deal has close date and value", "opportunity",
                    (won["actualclosedate"].isna() | ~(won["actualvalue"] > 0)).sum(), len(won)))
    out.append(_row("won deal has a sales order", "opportunity",
                    (~won["opportunityid"].isin(set(t["salesorder"]["opportunityid"]))).sum(), len(won)))
    out.append(_row("lost deal has a loss reason", "opportunity", lost["ahn_lossreason"].isna().sum(),
                    len(lost), "warning"))
    closed = opp[opp["statecode"] > 0]
    out.append(_row("close date not before creation", "opportunity",
                    (closed["actualclosedate"] < closed["createdon"].dt.normalize()).sum(), len(closed)))

    lead = t["lead"]
    qualified = lead[lead["statecode"] == 1]
    out.append(_row("qualified lead points at its opportunity", "lead",
                    qualified["qualifyingopportunityid"].isna().sum(), len(qualified)))

    quote = t["quote"]
    out.append(_row("quote valid-to not before valid-from", "quote",
                    (quote["effectiveto"] < quote["effectivefrom"]).sum(), len(quote)))

    lines = t["salesorderdetail"]
    diff = (lines["baseamount"] - lines["manualdiscountamount"] - lines["extendedamount"]).abs()
    out.append(_row("line amount = base - discount", "salesorderdetail", (diff > 1).sum(), len(lines)))
    totals = lines.groupby("salesorderid")["extendedamount"].sum()
    so = t["salesorder"].set_index("salesorderid")
    gap = (so["totalamount"] - totals.reindex(so.index)).abs()
    out.append(_row("order total = sum of its lines", "salesorder", (gap > 5_000).sum(), len(so), "warning"))

    inv = t["invoice"]
    paid = inv[inv["statecode"] == 2]
    out.append(_row("paid invoice has a payment date", "invoice", paid["ahn_paidon"].isna().sum(), len(paid)))
    return pd.DataFrame(out)


class DataQualityError(RuntimeError):
    pass


def assert_passes(results: pd.DataFrame) -> None:
    failed = results[results["status"] == "fail"]
    if not failed.empty:
        lines = [f"{r.entity}: {r.check} ({r.failed} of {r.total} rows)" for r in failed.itertuples()]
        raise DataQualityError("Data-quality errors:\n  " + "\n  ".join(lines))
