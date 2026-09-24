"""Predict which open deals will close, and compare with the CRM's own numbers.

Dynamics 365 already carries a win probability on every opportunity: the
`closeprobability` the rep types in, which in most teams follows the sales
stage more than the deal. This model learns from closed deals instead,
using only what is known early in a deal's life: customer industry, how the
deal came in, product group, size, the rep, how many hours the first quote
took, its discount, early activity, whether a competitor is involved and
which way steel prices were moving.

Evaluation is out of time: train on deals closed before the test window,
score the deals closed inside it, and compare against the probability the
rep had on the same deals at their last weekly pipeline snapshot. The
production model is then refit on every closed deal and scores the open
pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, roc_auc_score

CATEGORICAL = ["industry", "channel", "product_group", "owner"]
NUMERIC = ["log_tons", "hours_to_first_quote", "first_quote_discount", "activities_first_3d",
           "has_competitor", "is_repeat", "price_trend", "never_quoted"]
FEATURE_NAMES = {
    "industry": "Customer industry", "channel": "Lead channel / repeat business",
    "product_group": "Product group", "owner": "Sales rep", "log_tons": "Deal size (tons)",
    "hours_to_first_quote": "Hours to first quote", "first_quote_discount": "Discount on first quote",
    "activities_first_3d": "Activities in first 3 days", "has_competitor": "Competitor involved",
    "is_repeat": "Repeat customer", "price_trend": "Steel price trend", "never_quoted": "Never quoted",
}


@dataclass
class WinModelResult:
    scored: pd.DataFrame  # fact_opportunity with p_model
    metrics: dict
    calibration: pd.DataFrame
    importance: pd.DataFrame


class _Encoder:
    def fit(self, X: pd.DataFrame) -> "_Encoder":
        self.levels = {c: {v: i for i, v in enumerate(sorted(X[c].dropna().unique()))} for c in CATEGORICAL}
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        out = X[CATEGORICAL + NUMERIC].copy()
        for c in CATEGORICAL:
            out[c] = out[c].map(self.levels[c]).astype(float)
        return out.astype(float)


def build_features(fo: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    X = fo[["opportunityid", "won", "close_date", "state", "rep_prob_last"] + CATEGORICAL].copy()
    X["channel"] = X["channel"].fillna("Unknown")
    X["log_tons"] = np.log(fo["tons"].clip(lower=1))
    quoted = fo["first_quote_at"].notna()
    elapsed = (as_of - fo["created"]).dt.total_seconds() / 3600
    # An open deal without a quote yet has waited at least `elapsed` hours.
    X["hours_to_first_quote"] = np.where(quoted, fo["hours_to_first_quote"],
                                         np.where(fo["state"] == "Open", elapsed, np.nan))
    X["never_quoted"] = (~quoted & (fo["state"] != "Open")).astype(float)
    X["first_quote_discount"] = fo["first_quote_discount"]
    X["activities_first_3d"] = fo["activities_first_3d"]
    X["has_competitor"] = fo["competitor"].notna().astype(float)
    X["is_repeat"] = fo["is_repeat"].astype(float)
    X["price_trend"] = fo["price_trend"]
    return X


def _model() -> HistGradientBoostingClassifier:
    cat_mask = [c in CATEGORICAL for c in CATEGORICAL + NUMERIC]
    return HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=30, l2_regularization=1.0,
        categorical_features=cat_mask, random_state=42)


def _calibration(y: pd.Series, p: pd.Series, bins=(0, 0.2, 0.4, 0.6, 0.8, 1.0001)) -> pd.DataFrame:
    df = pd.DataFrame({"y": y.to_numpy(), "p": p.to_numpy()}).dropna()
    df["bin"] = pd.cut(df["p"], bins, right=False)
    g = df.groupby("bin", observed=False).agg(predicted=("p", "mean"), actual=("y", "mean"), deals=("y", "size"))
    g.index = [f"{int(b.left * 100)}-{min(int(round(b.right * 100)), 100)}%" for b in g.index]
    return g


def train_and_score(fo: pd.DataFrame, as_of: pd.Timestamp, test_days: int = 181) -> WinModelResult:
    X = build_features(fo, as_of)
    split = as_of - pd.Timedelta(days=test_days)
    closed = X[X["won"].notna()]
    train, test = closed[closed["close_date"] < split], closed[closed["close_date"] >= split]

    enc = _Encoder().fit(train)
    model = _model().fit(enc.transform(train), train["won"].astype(int))
    p_test = pd.Series(model.predict_proba(enc.transform(test))[:, 1], index=test.index)

    rep_ok = test["rep_prob_last"].notna()
    y = test["won"].astype(int)
    metrics = {
        "split_date": split.date().isoformat(), "n_train": int(len(train)), "n_test": int(len(test)),
        "test_win_rate": float(y.mean()),
        "auc_model": float(roc_auc_score(y, p_test)),
        "auc_crm": float(roc_auc_score(y[rep_ok], test.loc[rep_ok, "rep_prob_last"])),
        "brier_model": float(brier_score_loss(y, p_test)),
        "brier_crm": float(brier_score_loss(y[rep_ok], test.loc[rep_ok, "rep_prob_last"])),
        "mean_p_model": float(p_test.mean()), "mean_p_crm": float(test["rep_prob_last"].mean()),
    }
    cal_model = _calibration(y, p_test)
    cal_crm = _calibration(y[rep_ok], test.loc[rep_ok, "rep_prob_last"])
    calibration = cal_model.join(cal_crm, lsuffix="_model", rsuffix="_crm")

    imp = permutation_importance(model, enc.transform(test), y, scoring="roc_auc", n_repeats=8,
                                 random_state=42)
    importance = pd.DataFrame({"feature": [FEATURE_NAMES[c] for c in CATEGORICAL + NUMERIC],
                               "importance": imp.importances_mean}).sort_values("importance", ascending=False)

    # refit on every closed deal, score the open pipeline
    enc_all = _Encoder().fit(closed)
    final = _model().fit(enc_all.transform(closed), closed["won"].astype(int))
    scored = fo.copy()
    scored["p_model"] = np.nan
    scored.loc[test.index, "p_model"] = p_test
    open_ = X[X["state"] == "Open"]
    if len(open_):
        scored.loc[open_.index, "p_model"] = final.predict_proba(enc_all.transform(open_))[:, 1]
    return WinModelResult(scored, metrics, calibration, importance)
