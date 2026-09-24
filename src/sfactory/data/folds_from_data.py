"""Fold configuration from the data span (v1 D-008 kept in v2): holdout = the last 20% of the span, at least
18 months; the first decision point leaves `min_is_years` of history for the first IS window.
Accepts dates or datetimes (intraday data); DPs are always calendar dates at 00:00 of the run's clock."""
from __future__ import annotations

from datetime import date, datetime

from sfactory.timeline.folds import FoldConfig, add_months


def _as_date(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def fold_config_for(first: date, last: date, is_years: int = 3, dp_months: int = 6, holdout_frac: float = 0.2,
                    holdout_min_months: int = 18, min_is_years: int = 3) -> FoldConfig:
    first, last = _as_date(first), _as_date(last)
    data_end = date(last.year, last.month, 1) if last.day == 1 else add_months(date(last.year, last.month, 1), 1)
    span_months = (data_end.year - first.year) * 12 + data_end.month - first.month
    hold_months = max(holdout_min_months, round(span_months * holdout_frac))
    holdout_start = add_months(data_end, -hold_months)
    first_dp = add_months(date(first.year, first.month, 1), 12 * min_is_years + 1)
    if first_dp >= holdout_start:
        raise ValueError(f"data span {first}..{last} too short for {min_is_years}y IS + {hold_months}m holdout")
    return FoldConfig(first, first_dp, holdout_start, data_end, dp_months=dp_months, is_years=is_years)
