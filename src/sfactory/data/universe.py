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


def eligible_top_liquidity(dp: date, bars: pl.DataFrame, top_n: int = 500, min_price: float = 5.0,
                           lookback: int = 60, min_history: int = 250) -> list[str]:
    """Point-in-time universe without an index file: the top-N symbols by trailing median dollar volume,
    from bars strictly before the DP. Survivorship-free as long as the bar data keeps delisted symbols."""
    hist = bars.filter(pl.col("date") < dp)
    stats = (hist.sort("date").group_by("symbol").agg([
        pl.len().alias("n"),
        pl.col("close").last().alias("last_close"),
        pl.col("date").last().alias("last_date"),
        (pl.col("close") * pl.col("volume")).tail(lookback).median().alias("dv"),
    ]))
    recent = hist["date"].max() if hist.height else dp
    ok = stats.filter((pl.col("n") >= min_history) & (pl.col("last_close") >= min_price)
                      & (pl.col("last_date") >= recent))          # still trading at the DP
    return sorted(ok.sort(["dv", "symbol"], descending=[True, False]).head(top_n)["symbol"].to_list())


def universe_at(cfg, dp: date, bars: pl.DataFrame, membership: pl.DataFrame | None) -> list[str]:
    """Universe rule of a row: index membership (default) or top liquidity (no membership file)."""
    if getattr(cfg, "universe_mode", "membership") == "top_liquidity" or membership is None:
        return eligible_top_liquidity(dp, bars, cfg.universe_top_n, cfg.min_price, min_history=cfg.min_history)
    return eligible_at(dp, bars, membership, cfg.min_price, cfg.min_dollar_vol, min_history=cfg.min_history)
