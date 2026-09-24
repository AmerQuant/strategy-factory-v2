"""Intraday bar handling (design ch. 2, 4H from 1H with broker alignment).

Time convention for intraday bars: `date` is a naive Datetime = bar START, in the run's clock (UTC by default).
Every "before the DP" rule in the platform compares bar starts with a DP at 00:00 of the clock, so no bar may
straddle midnight of that clock (`check_no_straddle`); otherwise the last bar "before" a DP would close after it.

Broker alignment: brokers build 4H / daily bars on their own day (MT5 servers: 17:00 New York = 00:00 server
time). `resample_bars(..., clock_shift="7h")` moves the clock so that day boundary is midnight; windows are then
anchored to that midnight and never straddle it. The whole run must use the same clock (DPs, rollover days for
swap, calendar days). `alignment_variants` produces the sensitivity set required by the design.
"""
from __future__ import annotations

from datetime import timedelta

import polars as pl

OHLCV = [pl.col("open").first(), pl.col("high").max(), pl.col("low").min(), pl.col("close").last(),
         pl.col("volume").sum()]


def _dur(every: str) -> timedelta:
    n, unit = int(every[:-1]), every[-1]
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def is_intraday(bars: pl.DataFrame) -> bool:
    return isinstance(bars.schema["date"], pl.Datetime)


def shift_clock(bars: pl.DataFrame, clock_shift: str) -> pl.DataFrame:
    """Move timestamps by `clock_shift` (e.g. '7h': UTC 17:00 -> 00:00 for a NY-close broker day)."""
    if clock_shift in ("", "0h"):
        return bars
    sign = -1 if clock_shift.startswith("-") else 1
    return bars.with_columns(pl.col("date") + sign * _dur(clock_shift.lstrip("-+")))


def resample_bars(bars: pl.DataFrame, every: str, clock_shift: str = "0h") -> pl.DataFrame:
    """Aggregate intraday bars per symbol into `every` windows anchored at midnight of the (shifted) clock.

    Only bars whose start falls in a window contribute; label = window start. Incomplete windows are kept
    (sessions shorter than the window) because their close is final when the next window starts.
    """
    if not is_intraday(bars):
        raise ValueError("resample_bars needs intraday bars (Datetime `date`)")
    b = shift_clock(bars, clock_shift).sort(["symbol", "date"])
    out = (b.group_by_dynamic("date", every=every, group_by="symbol", label="left", closed="left",
                              start_by="window").agg(OHLCV).sort(["symbol", "date"]))
    return out.select("symbol", "date", "open", "high", "low", "close", "volume")


def to_daily(bars: pl.DataFrame, clock_shift: str = "0h") -> pl.DataFrame:
    """Daily bars (Date) from intraday bars on the (shifted) clock's calendar day."""
    b = shift_clock(bars, clock_shift).sort(["symbol", "date"])
    return (b.group_by(["symbol", pl.col("date").cast(pl.Date)], maintain_order=True).agg(OHLCV)
            .sort(["symbol", "date"]).select("symbol", "date", "open", "high", "low", "close", "volume"))


def check_no_straddle(bars: pl.DataFrame, every: str) -> None:
    """Raise if any bar [start, start + every) crosses midnight of the clock."""
    if not is_intraday(bars):
        return
    end = pl.col("date") + _dur(every) - timedelta(microseconds=1)
    bad = bars.filter(pl.col("date").cast(pl.Date) != end.cast(pl.Date))
    if len(bad):
        raise ValueError(f"{len(bad)} {every} bars straddle midnight (first {bad['date'][0]}); "
                         "use clock_shift so windows align with the day boundary")


def alignment_variants(bars: pl.DataFrame, every: str = "4h", shifts=("0h", "1h", "2h", "3h")) -> dict:
    """Same data resampled on shifted clocks: the design's alignment-sensitivity set."""
    return {s: resample_bars(bars, every, s) for s in shifts}
