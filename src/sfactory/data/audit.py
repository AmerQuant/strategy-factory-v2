"""Data audit (design P1): per-symbol checks on canonical daily bars before any research run.

Critical issues exclude a symbol; warnings are reported in the evidence package and never weight results.
"""
from __future__ import annotations

import polars as pl

CRITICAL = ("nonpositive_price", "ohlc_inconsistent", "duplicate_date")
WARNING = ("price_spike", "stale_close", "zero_volume", "long_gap")


def audit_bars(bars: pl.DataFrame, spike: float = 0.5, stale_run: int = 10, gap_days: int = 10) -> pl.DataFrame:
    """One row per (symbol, issue) with a count; spikes = |return| > spike that reverts next bar."""
    b = bars.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("r"),
        (pl.col("date") - pl.col("date").shift(1).over("symbol")).dt.total_days().alias("gap"),
    ).with_columns(pl.col("r").shift(-1).over("symbol").alias("r_next"))
    checks = {
        "nonpositive_price": pl.min_horizontal("open", "high", "low", "close") <= 0,
        "ohlc_inconsistent": (pl.col("high") < pl.col("low"))
        | (pl.max_horizontal("open", "close") > pl.col("high") * (1 + 1e-9))
        | (pl.min_horizontal("open", "close") < pl.col("low") * (1 - 1e-9)),
        "price_spike": (pl.col("r").abs() > spike) & (pl.col("r_next") * pl.col("r") < 0)
        & (pl.col("r_next").abs() > spike / 2),
        "zero_volume": pl.col("volume") <= 0,
        "long_gap": pl.col("gap") > gap_days,
    }
    rows = []
    for name, expr in checks.items():
        cnt = b.group_by("symbol").agg(expr.fill_null(False).sum().alias("count")).filter(pl.col("count") > 0)
        rows.append(cnt.with_columns(pl.lit(name).alias("issue")))
    dup = b.group_by(["symbol", "date"]).len().filter(pl.col("len") > 1).group_by("symbol").agg(
        pl.len().cast(pl.UInt32).alias("count")).with_columns(pl.lit("duplicate_date").alias("issue"))
    rows.append(dup)
    runs = (b.with_columns((pl.col("close") != pl.col("close").shift(1).over("symbol")).fill_null(True)
                           .cum_sum().over("symbol").alias("run"))
            .group_by(["symbol", "run"]).len().filter(pl.col("len") >= stale_run)
            .group_by("symbol").agg(pl.len().cast(pl.UInt32).alias("count"))
            .with_columns(pl.lit("stale_close").alias("issue")))
    rows.append(runs)
    out = pl.concat([r.select("symbol", "issue", pl.col("count").cast(pl.Int64)) for r in rows])
    return out.with_columns(pl.when(pl.col("issue").is_in(CRITICAL)).then(pl.lit("critical"))
                            .otherwise(pl.lit("warning")).alias("severity")).sort(["symbol", "issue"])


def audit_summary(issues: pl.DataFrame, n_symbols: int) -> dict:
    crit = sorted(issues.filter(pl.col("severity") == "critical")["symbol"].unique().to_list())
    by = issues.group_by("issue").agg(pl.col("symbol").n_unique().alias("symbols")).sort("issue")
    return {"symbols": n_symbols, "excluded_critical": crit,
            "issues": {r["issue"]: r["symbols"] for r in by.iter_rows(named=True)}}
