import json

import numpy as np
import pytest

from steel_crm.analytics import accounts, marketing, sales
from steel_crm.models.win_probability import train_and_score
from steel_crm.pipeline import run


@pytest.fixture(scope="module")
def model(warehouse):
    return train_and_score(warehouse.fact_opportunity, warehouse.as_of, test_days=91)


def test_order_lines_add_up(warehouse):
    lines = warehouse.fact_sales_line
    assert np.allclose(lines["margin"], lines["net"] - lines["cost"])
    assert (lines["net"] <= lines["gross"] + 1).all()
    assert (lines["tons"] > 0).all()


def test_funnel_narrows_at_every_stage(warehouse):
    counts = marketing.funnel(warehouse)["count"].tolist()
    assert counts == sorted(counts, reverse=True)


def test_channel_totals_match_campaigns(warehouse):
    campaigns, channels = marketing.campaign_roi(warehouse)
    for col in ("cost", "leads", "won_deals", "acquired_revenue"):
        assert campaigns[col].sum() == pytest.approx(channels[col].sum())
    total_revenue = warehouse.fact_sales_line["net"].sum()
    assert channels["acquired_revenue"].sum() <= total_revenue


def test_aging_buckets_sum_to_open_receivables(warehouse):
    ar = accounts.receivables(warehouse)
    buckets = ar["aging"].drop(columns="industry").to_numpy().sum()
    assert buckets == pytest.approx(ar["open_ar"])
    assert 0 <= ar["overdue_share"] <= 1


def test_at_risk_customers_are_regulars_gone_quiet(warehouse):
    risk, summary = accounts.at_risk(warehouse, n=50)
    assert (risk["orders"] >= 3).all()
    assert (risk["days_silent"] >= accounts.AT_RISK_MIN_DAYS).all()
    assert summary["accounts"] <= summary["regular_customers"]


def test_quote_speed_matters(warehouse):
    speed = sales.win_analysis(warehouse.fact_opportunity)["quote_speed"].set_index("bucket")
    assert speed.loc["Under 4 hours", "win_rate"] > speed.loc["Over 3 days", "win_rate"]


def test_win_model_beats_chance_and_scores_open_deals(model):
    assert model.metrics["auc_model"] > 0.6
    open_p = model.scored.loc[model.scored["state"] == "Open", "p_model"]
    assert open_p.notna().all() and open_p.between(0, 1).all()
    assert model.importance["feature"].iloc[0]


def test_pipeline_writes_dashboard_data(export_dir, tmp_path):
    js = tmp_path / "data.js"
    summary = run(export_dir, tmp_path / "processed", js)
    text = js.read_text(encoding="utf-8")
    payload = json.loads(text[text.index("{"): text.rindex("}") + 1])
    assert payload["kpi"]["revenue"] > 0
    assert len(payload["insights"]) >= 4
    assert (tmp_path / "processed" / "steel_crm.db").exists()
    assert (tmp_path / "processed" / "powerbi" / "fact_opportunity.csv").exists()
    assert summary["model"]["n_test"] > 0
