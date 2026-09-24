import shutil
import uuid

import pandas as pd
import pytest

from steel_crm.ingest.dataverse import METADATA_FILES, SCHEMA, ExportSchemaError, load_export


def test_every_table_and_metadata_file_is_written(export_dir):
    for name in list(SCHEMA) + METADATA_FILES:
        assert (export_dir / f"{name}.csv").exists(), name


def test_keys_are_guids_and_timestamps_are_utc(export_dir):
    opp = pd.read_csv(export_dir / "opportunity.csv")
    assert opp["opportunityid"].is_unique
    for value in opp["opportunityid"].head(20):
        uuid.UUID(value)
    assert opp["createdon"].str.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$").all()
    assert opp["estimatedclosedate"].str.match(r"^\d{4}-\d{2}-\d{2}$").all()


def test_option_sets_and_states_are_decoded(export):
    opp = export["opportunity"]
    assert set(opp["statecode_label"]) <= {"Open", "Won", "Lost"}
    assert opp["ahn_productgroup_label"].notna().all()
    lost = opp[opp["statecode"] == 2]
    assert lost["ahn_lossreason_label"].notna().all()
    assert export["lead"]["leadsourcecode_label"].notna().all()
    assert not export.unmapped_codes


def test_times_are_converted_to_tehran_business_hours(export):
    hours = export["lead"]["createdon"].dt.hour
    # generated between 08:00 and 17:00 local time; in UTC they would start at 04:30
    assert hours.min() >= 8 and hours.max() <= 17


def test_missing_column_fails_loudly(export_dir, tmp_path):
    broken = tmp_path / "broken"
    shutil.copytree(export_dir, broken)
    opp = pd.read_csv(broken / "opportunity.csv").drop(columns=["estimatedvalue"])
    opp.to_csv(broken / "opportunity.csv", index=False)
    with pytest.raises(ExportSchemaError, match="estimatedvalue"):
        load_export(broken)


def test_won_deals_have_orders_and_quotes_expire_fast(export):
    opp, so = export["opportunity"], export["salesorder"]
    won = opp[opp["statecode"] == 1]
    assert won["opportunityid"].isin(set(so["opportunityid"])).all()
    q = export["quote"]
    validity = (q["effectiveto"] - q["effectivefrom"]).dt.days
    assert validity.max() <= 7
