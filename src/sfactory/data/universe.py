"""Point-in-time eligibility (stage S1). Uses only bars strictly before the decision point."""
from __future__ import annotations

from datetime import date

import polars as pl


def eligible_at(dp: date, bars: pl.DataFrame, membership: pl.DataFrame, min_price: float = 5.0,
                min_dollar_vol: float = 1e6, lookback: int = 20, min_history: int = 250) -> list[str]:
    members = membership.filter((pl.col("start") <= dp) & (pl.col("end").is_null() | (pl.col("end") > dp)))
    hist = bars.filter((pl.col("date") < dp) & pl.col("symbol").is_in(members["symbol"].to_list()))
    stats = (hist.sort("date").group_by("symbol").agg([
        pl.len().alias("n"),
        pl.col("close").last().alias("last_close"),  # split-only level (invariant #5)
        (pl.col("close") * pl.col("volume")).tail(lookback).median().alias("dv"),
    ]))
    ok = stats.filter((pl.col("n") >= min_history) & (pl.col("last_close") >= min_price) &
                      (pl.col("dv") >= min_dollar_vol))
    return sorted(ok["symbol"].to_list())


def static_membership(bars: pl.DataFrame) -> pl.DataFrame:
    """Membership for symbol-based universes without an index (FX, metals, CFD indices): each symbol is a
    member from its first bar, with no end date. Eligibility still requires history before the DP."""
    return (bars.group_by("symbol").agg(pl.col("date").min().alias("start"))
            .with_columns(pl.lit(None, dtype=pl.Date).alias("end")).sort("symbol"))
