"""Turn the analysis into a handful of plain sentences for the dashboard.

Every sentence is computed from this run's data, so it stays true when the
export changes; a finding that no longer holds is dropped rather than
reworded into something false.
"""

from __future__ import annotations

import pandas as pd


def irr(v: float) -> str:
    """Compact IRR amount: 1.2T, 850B, 40M."""
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= div:
            return f"{v / div:,.1f}{unit} IRR" if abs(v) < 100 * div else f"{v / div:,.0f}{unit} IRR"
    return f"{v:,.0f} IRR"


def build_insights(
    k: dict,
    win: dict,
    model: dict,
    calibration: pd.DataFrame,
    channels: pd.DataFrame,
    monthly: pd.DataFrame,
    ar_ind: pd.DataFrame,
    risk: dict,
    reps: pd.DataFrame,
    disc_margin: pd.DataFrame,
) -> list[str]:
    out = []

    speed = win["quote_speed"].set_index("bucket")
    fast, slow = speed.loc["Under 4 hours"], speed.loc["Over 3 days"]
    if fast["win_rate"] > slow["win_rate"]:
        out.append(
            f"Speed wins steel deals: quotes sent <b>within 4 hours win {fast['win_rate']:.0%}</b> of the time, "
            f"quotes sent after 3 days only {slow['win_rate']:.0%}. The median first quote takes "
            f"{k['median_hours_to_quote']:.0f} hours, and {k['quoted_within_24h']:.0%} go out within a day."
        )

    cal = calibration.set_index("bin")
    top_bin = cal.loc["80-100%"]
    if top_bin["deals_crm"] and top_bin["actual_crm"] < top_bin["predicted_crm"] - 0.05:
        out.append(
            f"Deals reps rated <b>80% or higher</b> in the CRM closed only <b>{top_bin['actual_crm']:.0%}</b> of the "
            f"time; across the last six months the CRM's probabilities averaged {model['mean_p_crm']:.0%} on deals "
            f"that closed at {model['test_win_rate']:.0%}. The win model ranks deals far better "
            f"(AUC {model['auc_model']:.2f} vs {model['auc_crm']:.2f})."
        )

    d = win["discount"].set_index("bucket")
    dm = disc_margin.set_index("bucket")
    if d.loc["4%+", "win_rate"] < d.loc["1-2%", "win_rate"]:
        out.append(
            f"Deep discounts don't buy wins: first quotes above 4% off win {d.loc['4%+', 'win_rate']:.0%} vs "
            f"{d.loc['1-2%', 'win_rate']:.0%} at 1-2%, because they go to contested deals, and they cut margin "
            f"per ton from {irr(dm.loc['1-2%', 'margin_per_ton'])} to <b>{irr(dm.loc['4%+', 'margin_per_ton'])}</b>."
        )

    ch = channels.set_index("channel")
    best_first = ch["first_deal_roi"].idxmax()
    worst_first = ch["first_deal_roi"].idxmin()
    if ch.loc[worst_first, "first_deal_roi"] < 0:
        out.append(
            f"<b>{best_first}</b> pays back {ch.loc[best_first, 'first_deal_roi'] + 1:.1f}x its cost on first deals "
            f"alone; <b>{worst_first}</b> loses money on first deals ({ch.loc[worst_first, 'first_deal_roi']:.0%}) and "
            f"only earns its keep through the repeat orders of the "
            f"{int(ch.loc[worst_first, 'acquired_accounts'])} accounts it brought in."
        )

    m = monthly.dropna(subset=["price_index"]).copy()
    m["jump"] = m["price_index"].pct_change()
    if m["jump"].notna().any():
        top = m.loc[m["jump"].idxmax()]
        avg = m["margin_per_ton"].mean()
        low = m.loc[m["margin_per_ton"].idxmin()]
        out.append(
            f"When list prices jumped {top['jump']:.0%} in {pd.Period(top['month']).strftime('%b %Y')}, margin per ton "
            f"hit {irr(top['margin_per_ton'])} ({top['margin_per_ton'] / avg:.1f}x the average): stock bought at the "
            f"previous month's prices. The weakest month, {pd.Period(low['month']).strftime('%b %Y')}, made "
            f"{irr(low['margin_per_ton'])} per ton."
        )

    a = ar_ind.set_index("industry")
    slow = a["avg_days_late"].idxmax()
    out.append(
        f"<b>{slow}</b> customers pay {a.loc[slow, 'avg_days_late']:.0f} days after the due date on average and "
        f"run {a.loc[slow, 'dso']:.0f} days of sales outstanding, against {k['dso']:.0f} for the whole book. "
        f"Across all customers, {k['overdue_share']:.0%} of open receivables are overdue."
    )

    if risk["accounts"]:
        out.append(
            f"<b>{risk['accounts']} of {risk['regular_customers']} regular customers</b> have gone quiet for more "
            f"than 2.5x their usual reorder gap; they bought {irr(risk['revenue_prior_year'])} in the year before. "
            f"The win-back list is in the Customers section."
        )

    team_disc = reps["discount"].sum() / reps["gross"].sum()
    heavy = reps.loc[reps["avg_discount"].idxmax()]
    if heavy["avg_discount"] > team_disc * 1.3:
        cost = (heavy["avg_discount"] - team_disc) * heavy["gross"]
        out.append(
            f"{heavy['rep']} discounts {heavy['avg_discount']:.1%} on average against a team rate of {team_disc:.1%}, "
            f"about <b>{irr(cost)}</b> of margin in the last 12 months."
        )
    return out
