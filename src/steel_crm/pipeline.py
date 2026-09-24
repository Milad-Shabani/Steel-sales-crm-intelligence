"""End-to-end run: Dataverse export -> checks -> star schema -> analytics and
win model -> SQLite + Power BI CSVs + dashboard data."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .analytics import accounts, marketing, sales
from .analytics.insights import build_insights
from .ingest.dataverse import load_export
from .models.win_probability import train_and_score
from .quality.checks import assert_passes, run_checks
from .reporting.dashboard_data import records, write_dashboard_data
from .warehouse.star_schema import build_warehouse, write_warehouse

log = logging.getLogger(__name__)


def run(export_dir: Path = Path("data/dynamics_export"), out_dir: Path = Path("data/processed"),
        dashboard_js: Path = Path("dashboard/data.js")) -> dict:
    t0 = time.time()
    log.info("Loading the Dynamics 365 export from %s", export_dir)
    export = load_export(export_dir)
    log.info("Loaded %d tables, %s rows", len(export.tables), f"{sum(len(d) for d in export.tables.values()):,}")

    dq = run_checks(export)
    out_dir.mkdir(parents=True, exist_ok=True)
    dq.to_csv(out_dir / "data_quality_report.csv", index=False)
    assert_passes(dq)
    log.info("Data-quality checks: %d passed, %d warnings", (dq["status"] == "pass").sum(),
             (dq["status"] == "warn").sum())

    wh = build_warehouse(export)
    log.info("Training the win-probability model and scoring the open pipeline")
    model = train_and_score(wh.fact_opportunity, wh.as_of)
    wh.fact_opportunity = model.scored
    write_warehouse(wh, out_dir / "steel_crm.db", out_dir / "powerbi")
    (out_dir / "model_metrics.json").write_text(json.dumps(model.metrics, indent=2))

    campaigns, channels = marketing.campaign_roi(wh)
    ar = accounts.receivables(wh)
    k = sales.kpis(wh, model.scored, ar, campaigns)
    monthly = sales.monthly(wh)
    win = sales.win_analysis(model.scored)
    disc_margin = sales.discount_margin(wh)
    reps = sales.reps(wh, model.scored)
    risk, risk_summary = accounts.at_risk(wh)
    pipe = sales.pipeline(model.scored)
    insights = build_insights(k, win, model.metrics, model.calibration.reset_index(names="bin"), channels, monthly,
                              ar["by_industry"], risk_summary, reps, disc_margin)

    top_open = pipe["top"].merge(wh.dim_account[["accountid", "name"]], on="accountid")
    payload = {
        "as_of": wh.as_of, "kpi": k, "insights": insights, "model": model.metrics,
        "monthly": records(monthly), "funnel": records(marketing.funnel(wh)),
        "lead_sources": records(marketing.lead_sources(wh)),
        "channels": records(channels), "campaigns": records(campaigns.head(12), [
            "name", "channel", "cost", "leads", "won_deals", "first_deal_roi", "acquired_accounts",
            "acquired_revenue", "roi"]),
        "quote_speed": records(win["quote_speed"]), "discount": records(win["discount"]),
        "discount_margin": records(disc_margin), "industries": records(win["industry"]),
        "competitors": records(win["competitor"]), "loss_reasons": records(win["loss_reasons"]),
        "product_groups": records(sales.product_groups(wh)), "provinces": records(sales.provinces(wh)),
        "reps": records(reps), "pipeline_stage": records(pipe["by_stage"]),
        "pipeline_month": records(pipe["by_month"]),
        "pipeline_top": records(top_open, ["name", "owner", "product_group", "tons", "stage", "estimated_value",
                                           "crm_probability", "p_model", "hours_to_first_quote", "competitor"]),
        "calibration": records(model.calibration.reset_index(names="bin")),
        "importance": records(model.importance),
        "aging": records(ar["aging"]), "ar_industry": records(ar["by_industry"]),
        "top_overdue": records(ar["top_overdue"], ["name", "industry", "owner", "overdue", "invoices",
                                                   "oldest_days"]),
        "top_accounts": records(accounts.top_accounts(wh), ["name", "industry", "province", "owner", "revenue",
                                                            "tons", "orders", "growth", "last_order"]),
        "at_risk": records(risk, ["name", "industry", "province", "owner", "orders", "revenue", "last_order",
                                  "days_silent", "revenue_prior_year"]),
        "at_risk_summary": risk_summary,
        "data_quality": {"checks": int(len(dq)), "passed": int((dq["status"] == "pass").sum()),
                         "warnings": int((dq["status"] == "warn").sum()),
                         "rows": int(sum(len(d) for d in export.tables.values())),
                         "tables": int(len(export.tables))},
    }
    write_dashboard_data(payload, dashboard_js)
    log.info("Wrote %s, %s and the Power BI CSVs in %s", dashboard_js, out_dir / "steel_crm.db", out_dir / "powerbi")
    return {"seconds": round(time.time() - t0, 1), "kpi": k, "model": model.metrics, "insights": insights}
