import copy

import pandas as pd
import pytest

from steel_crm.analytics import accounts, operations
from steel_crm.processes.definitions import all_processes
from steel_crm.quality.checks import run_checks


@pytest.fixture(scope="module")
def ops(warehouse):
    dlv, cash, svc = operations.delivery(warehouse), operations.collections(warehouse), operations.service(warehouse)
    return dlv, cash, svc, operations.process_overlays(warehouse, dlv, cash, svc, accounts.receivables(warehouse))


def test_shipment_steps_follow_each_other(warehouse):
    s = warehouse.fact_shipment
    d = s[s["delivered"].notna()]
    for a, b in [("order_date", "ready"), ("ready", "loaded"), ("loaded", "dispatched"), ("dispatched", "delivered")]:
        assert (d[b] >= d[a]).all(), (a, b)
    assert (d["otif"].astype(bool) == (d["on_time"].astype(bool) & d["in_full"].astype(bool))).all()
    assert s["salesorderid"].is_unique


def test_delivery_and_service_measures_hold_together(warehouse, ops):
    dlv, cash, svc, _ = ops
    k = dlv["kpi"]
    assert 0 < k["otif"] <= min(k["on_time"], k["in_full"]) <= 1
    assert k["shipments"] == dlv["by_warehouse"]["shipments"].sum() == sum(b["loads"] for b in dlv["weight_hist"])
    s = svc["kpi"]
    assert s["cases"] == svc["by_category"]["cases"].sum() > 0
    for share in ("response_sla", "resolve_sla", "upheld", "escalated"):
        assert 0 <= s[share] <= 1
    # the churn comparison splits the regular customers into three groups
    orders = warehouse.fact_sales_line.groupby("accountid")["salesorderid"].nunique()
    assert sum(g["customers"] for g in svc["churn"].values()) == (orders >= 3).sum()
    assert 0 < cash["paid_on_time"] <= 1


def test_every_process_step_with_a_number_exists(ops):
    overlays = ops[3]
    nodes = {n.id for p in all_processes() for n in p.nodes}
    assert set(overlays) <= nodes
    for p in all_processes():
        assert sum(n.id in overlays for n in p.nodes) >= 8, p.id


def test_lead_to_cash_phases(warehouse):
    l2c = operations.lead_to_cash(warehouse)
    assert [p["phase"] for p in l2c["phases"]][0] == "Lead to qualified"
    assert all(p["days"] >= 0 for p in l2c["phases"])
    assert l2c["end_to_end_days"] > sum(p["days"] for p in l2c["phases"][:3])


def test_shipment_loaded_after_dispatch_is_caught(export):
    broken = copy.deepcopy(export)
    sh = broken.tables["ahn_shipment"]
    i = sh.index[sh["ahn_deliveredon"].notna()][0]
    sh.loc[i, "ahn_loadedon"] = sh.loc[i, "ahn_deliveredon"] + pd.Timedelta(hours=5)
    results = run_checks(broken)
    row = results[results["check"].str.startswith("shipment steps in order")]
    assert row["failed"].iloc[0] >= 1 and row["status"].iloc[0] == "fail"
