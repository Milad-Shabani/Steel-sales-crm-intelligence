import copy

import pytest

from steel_crm.quality.checks import DataQualityError, assert_passes, run_checks


def test_generated_export_passes_every_check(export):
    results = run_checks(export)
    assert len(results) > 40
    assert (results["status"] == "fail").sum() == 0
    assert_passes(results)


def test_orphan_lookup_is_caught(export):
    broken = copy.deepcopy(export)
    opp = broken.tables["opportunity"]
    opp.loc[opp.index[0], "customerid"] = "00000000-0000-4000-8000-000000000000"
    results = run_checks(broken)
    row = results[(results["entity"] == "opportunity") & (results["check"] == "customerid -> account")]
    assert row["failed"].iloc[0] == 1
    with pytest.raises(DataQualityError, match="customerid -> account"):
        assert_passes(results)


def test_won_deal_without_value_is_caught(export):
    broken = copy.deepcopy(export)
    opp = broken.tables["opportunity"]
    won = opp.index[opp["statecode"] == 1][0]
    opp.loc[won, "actualvalue"] = None
    results = run_checks(broken)
    assert results.loc[results["check"] == "won deal has close date and value", "failed"].iloc[0] == 1
