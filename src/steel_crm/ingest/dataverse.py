"""Read a Dynamics 365 Sales export (Dataverse tables as CSV) into DataFrames.

Three things turn the raw export into something analysable:

* schema check: every table must carry the columns the pipeline relies on,
  so a changed export fails loudly here instead of producing quiet zeros
  further down;
* option-set decoding: Dataverse stores choices as integer codes. Each coded
  column gets a readable `<column>_label` next to it, from the metadata
  files Synapse Link writes alongside the tables (`OptionsetMetadata.csv`,
  `GlobalOptionsetMetadata.csv`, `StateMetadata.csv`, `StatusMetadata.csv`);
* time zones: Dataverse keeps date-times in UTC. They are converted to
  Tehran time before anything is bucketed by day or month, otherwise a deal
  closed on a Saturday evening would land on the wrong day. Date-only
  columns (estimated close date, due date, ...) are left as dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

LOCAL_TZ = "Asia/Tehran"

# Columns each table must have for the pipeline to run
SCHEMA: dict[str, list[str]] = {
    "systemuser": ["systemuserid", "fullname", "title", "territoryid"],
    "territory": ["territoryid", "name"],
    "product": ["productid", "productnumber", "name", "ahn_productgroup"],
    "pricelevel": ["pricelevelid", "name", "begindate", "enddate"],
    "productpricelevel": ["pricelevelid", "productid", "amount"],
    "competitor": ["competitorid", "name"],
    "account": ["accountid", "name", "industrycode", "address1_stateorprovince", "territoryid", "ownerid",
                "paymenttermscode", "creditlimit", "originatingleadid", "createdon", "statecode"],
    "campaign": ["campaignid", "name", "ahn_channel", "typecode", "actualstart", "actualend",
                 "budgetedcost", "totalactualcost"],
    "campaignresponse": ["activityid", "regardingobjectid", "responsecode", "receivedon"],
    "lead": ["leadid", "companyname", "industrycode", "address1_stateorprovince", "leadsourcecode",
             "campaignid", "ahn_productgroup", "ahn_estimatedtonnage", "parentaccountid",
             "qualifyingopportunityid", "ownerid", "createdon", "modifiedon", "statecode", "statuscode"],
    "opportunity": ["opportunityid", "customerid", "originatingleadid", "campaignid", "ownerid", "createdon",
                    "estimatedclosedate", "actualclosedate", "estimatedvalue", "actualvalue",
                    "closeprobability", "salesstage", "stepname", "msdyn_forecastcategory", "statecode",
                    "statuscode", "ahn_productgroup", "ahn_tonnage", "ahn_lossreason",
                    "ahn_isrepeatbusiness"],
    "opportunitycompetitors": ["opportunityid", "competitorid"],
    "quote": ["quoteid", "opportunityid", "revisionnumber", "createdon", "effectivefrom", "effectiveto",
              "discountpercentage", "totalamount", "statecode", "statuscode"],
    "salesorder": ["salesorderid", "opportunityid", "customerid", "ownerid", "submitdate", "datefulfilled",
                   "totalamount", "statecode"],
    "salesorderdetail": ["salesorderdetailid", "salesorderid", "productid", "quantity", "priceperunit",
                         "baseamount", "manualdiscountamount", "extendedamount", "ahn_costperton"],
    "invoice": ["invoiceid", "salesorderid", "customerid", "createdon", "duedate", "totalamount",
                "paymenttermscode", "ahn_paidon", "statecode"],
    "activitypointer": ["activityid", "activitytypecode", "regardingobjectid", "regardingobjecttypecode",
                        "ownerid", "createdon"],
    "ahn_pipelinesnapshot": ["ahn_snapshotdate", "ahn_opportunityid", "ownerid", "ahn_stepname",
                             "ahn_closeprobability", "ahn_estimatedvalue", "ahn_forecastcategory"],
}
METADATA_FILES = ["OptionsetMetadata", "GlobalOptionsetMetadata", "StateMetadata", "StatusMetadata"]

_UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]00:00)$")
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExportSchemaError(ValueError):
    pass


@dataclass
class DataverseExport:
    tables: dict[str, pd.DataFrame]
    unmapped_codes: list[dict] = field(default_factory=list)

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]


def _parse_times(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        # text columns are `object` in pandas 2 and `str` in pandas 3
        if not (pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col])):
            continue
        sample = df[col].dropna().astype(str).head(50)
        if sample.empty:
            continue
        if sample.map(lambda v: bool(_UTC_STAMP.match(v))).all():
            df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
        elif sample.map(lambda v: bool(_DATE_ONLY.match(v))).all():
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d")
    return df


def _option_maps(meta: dict[str, pd.DataFrame]) -> dict[tuple[str, str], dict[int, str]]:
    maps: dict[tuple[str, str], dict[int, str]] = {}
    for name in ("OptionsetMetadata", "GlobalOptionsetMetadata"):
        m = meta[name]
        m = m[m["LocalizedLabelLanguageCode"] == 1033]
        for (entity, column), g in m.groupby(["EntityName", "OptionSetName"]):
            maps[(entity, column)] = dict(zip(g["Option"].astype(int), g["LocalizedLabel"]))
    return maps


def _decode(tables, meta) -> list[dict]:
    unmapped = []
    maps = _option_maps(meta)
    for (entity, column), labels in maps.items():
        df = tables.get(entity)
        if df is None or column not in df.columns:
            continue
        codes = pd.to_numeric(df[column], errors="coerce").astype("Int64")
        df[column] = codes
        df[f"{column}_label"] = codes.map(labels)
        missing = codes.notna() & df[f"{column}_label"].isna()
        if missing.any():
            unmapped.append(dict(entity=entity, column=column, rows=int(missing.sum()),
                                 codes=sorted(set(codes[missing].astype(int)))))
    states, statuses = meta["StateMetadata"], meta["StatusMetadata"]
    for entity, df in tables.items():
        if "statecode" in df.columns:
            s = states[states["EntityName"] == entity]
            df["statecode_label"] = df["statecode"].astype("Int64").map(dict(zip(s["State"], s["LocalizedLabel"])))
        if "statuscode" in df.columns:
            s = statuses[statuses["EntityName"] == entity]
            df["statuscode_label"] = df["statuscode"].astype("Int64").map(dict(zip(s["Status"], s["LocalizedLabel"])))
    return unmapped


def load_export(export_dir: Path) -> DataverseExport:
    export_dir = Path(export_dir)
    missing_files = [n for n in list(SCHEMA) + METADATA_FILES if not (export_dir / f"{n}.csv").exists()]
    if missing_files:
        raise ExportSchemaError(f"Missing files in {export_dir}: {', '.join(missing_files)}")

    meta = {n: pd.read_csv(export_dir / f"{n}.csv") for n in METADATA_FILES}
    tables: dict[str, pd.DataFrame] = {}
    problems = []
    for name, required in SCHEMA.items():
        df = pd.read_csv(export_dir / f"{name}.csv", low_memory=False)
        absent = [c for c in required if c not in df.columns]
        if absent:
            problems.append(f"{name}: {', '.join(absent)}")
        tables[name] = _parse_times(df)
    if problems:
        raise ExportSchemaError("Export is missing required columns -> " + "; ".join(problems))

    unmapped = _decode(tables, meta)
    return DataverseExport(tables, unmapped)
