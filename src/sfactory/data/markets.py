"""Asset classes of the v1 store and the 24x5 trading calendar of FX / index / metal CFDs (Moneta, MT5).

v1 store classes (v1 `configs/universe/dukascopy.csv`): `fx`, `metal`, `index_cfd` (and `energy_cfd`, not used here).
Their snapshots are Dukascopy mid bars, `timeframe="1H"`, `adjustment="raw"`, `ts` = bar start in UTC. US equities
(`us_equity`) keep their own path in the scripts and are not handled here.

24x5 calendar: the whole run uses the MT5 server day, 17:00 New York = 00:00 all year (`data.resample.broker_clock`,
`clock_shift="ny"`). Sunday-evening New York bars fall on Monday, the rollover is midnight of the clock, and a
trading day is Monday to Friday. Bars that still land on Saturday or Sunday (quotes after Friday 17:00 New York or
before Sunday 17:00) are dropped and counted in the caveats. Holidays are whatever days have no bars. Daily bars are
built from the hourly ones on the same clock (`to_daily`); 4H bars are anchored to the same midnight.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from sfactory.data.resample import broker_clock, check_no_straddle, resample_bars, to_daily
from sfactory.data.sfac_store import StoreLoad, load_store

CFD_CLASSES = ("fx", "index_cfd", "metal")
ASSET_CLASSES = ("us_equity",) + CFD_CLASSES


@dataclass
class MarketLoad:
    bars: pl.DataFrame
    version: str
    load: StoreLoad
    caveats: list = field(default_factory=list)
    weekend_bars: int = 0


def load_market(store, asset_class: str, timeframe: str = "1D", resample: str | None = None,
                clock_shift: str = "ny", symbols: list[str] | None = None) -> MarketLoad:
    """Bars of a CFD class on the New York 17:00 broker clock. timeframe: 1D (from the hourly store) or 1H
    (optionally `resample`d, e.g. 4h). The clock is fixed: any clock_shift other than "ny" (or the scripts' "0h"
    default, meaning not given) is refused, because swap days, DPs and sessions all assume it."""
    if asset_class not in CFD_CLASSES:
        raise ValueError(f"load_market handles {CFD_CLASSES}; got {asset_class}")
    if clock_shift not in ("ny", "0h", ""):
        raise ValueError(f"{asset_class}: the calendar is the 17:00 New York broker day; clock_shift {clock_shift!r} "
                         "is not allowed (use ny)")
    if timeframe not in ("1D", "1H"):
        raise ValueError(f"{asset_class}: timeframe 1D or 1H (the store holds 1H bars)")
    ld = load_store(store, "1H", asset_class, symbols=symbols, require_adjustment="raw")
    b = broker_clock(ld.bars) if len(ld.bars) else ld.bars
    weekend = int(b.filter(pl.col("date").dt.weekday() >= 6).height) if len(b) else 0
    if weekend:
        b = b.filter(pl.col("date").dt.weekday() < 6)
    tag = "1D" if timeframe == "1D" else (resample or "1H")
    if timeframe == "1D":
        b = to_daily(b) if len(b) else b.with_columns(pl.col("date").cast(pl.Date))
    elif resample:
        b = resample_bars(b, resample, "0h")
        check_no_straddle(b, resample)
    else:
        check_no_straddle(b, "1h")
    caveats = [f"{asset_class}: no dividends (broker dividend adjustments on index CFDs are not modelled)",
               f"24x5 calendar on the 17:00 New York broker day; {weekend} weekend bars dropped"]
    version = f"{ld.version}-{asset_class}-ny-{tag}"
    return MarketLoad(b.sort(["symbol", "date"]), version, ld, caveats, weekend)
