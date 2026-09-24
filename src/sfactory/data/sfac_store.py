"""Adapter for the v1 Strategy Factory data store (``StrategyFactory_data/store``, ``SFAC_DATA_ROOT``).

Reads the v1 layout exactly as written by v1 (``strategy_factory.data.store`` / ``catalog``):

    <root>/catalog.parquet                                   one row per snapshot, ``is_reference``
    <root>/<source>/<symbol>/<timeframe>/<hash>.parquet      canonical bars: ts (UTC, bar start),
                                                             open, high, low, close, volume, ...

Only reference snapshots are read (one per symbol and timeframe), ``quality_status == critical``
is skipped, and the price basis must be ``split`` (v1 D-021: split-adjusted only), which is
exactly the v2 execution series. Daily bars are stamped at the session date 00:00 UTC, so the
date is ``ts.date()``. The v1 store holds no dividends: v2 then runs with ``adj_factor = 1``
(signal series = split-only) and the evidence records that caveat.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from sfactory.data.contracts import BARS_SCHEMA

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


def safe_component(name: str) -> str:
    """Same path rule as v1 ``store.safe_component``."""
    cleaned = _UNSAFE.sub("_", name).strip().rstrip(".")
    if cleaned.upper().split(".")[0] in _RESERVED:
        cleaned += "_"
    return cleaned


@dataclass
class StoreLoad:
    bars: pl.DataFrame
    symbols: list[str]
    skipped: dict = field(default_factory=dict)   # symbol -> reason
    caveats: list[str] = field(default_factory=list)
    version: str = ""                             # hash of the (symbol, snapshot_hash) pairs read


def load_store(root: str | Path, timeframe: str = "1D", asset_class: str = "us_equity",
               symbols: list[str] | None = None, require_adjustment: str = "split",
               allow_warning: bool = True) -> StoreLoad:
    root = Path(root)
    cat_path = root / "catalog.parquet"
    if not cat_path.is_file():
        raise FileNotFoundError(f"no catalog.parquet under {root} (expected the v1 SFAC_DATA_ROOT)")
    cat = pl.read_parquet(cat_path)
    if "quality_status" not in cat.columns:
        cat = cat.with_columns(pl.lit("unchecked").alias("quality_status"))
    sel = cat.filter(pl.col("is_reference") & (pl.col("timeframe") == timeframe)
                     & (pl.col("asset_class") == asset_class))
    if symbols is not None:
        sel = sel.filter(pl.col("symbol").is_in(symbols))
    out, skipped, used, keys = [], {}, [], []
    for r in sel.sort("symbol").iter_rows(named=True):
        sym = r["symbol"]
        if r["quality_status"] == "critical" or (r["quality_status"] == "warning" and not allow_warning):
            skipped[sym] = f"quality {r['quality_status']}"
            continue
        if require_adjustment and r["adjustment"] != require_adjustment:
            skipped[sym] = f"adjustment {r['adjustment']} (need {require_adjustment})"
            continue
        p = root / safe_component(r["source"]) / safe_component(sym) / timeframe / f"{r['snapshot_hash']}.parquet"
        if not p.is_file():
            skipped[sym] = "snapshot file missing"
            continue
        df = pl.read_parquet(p, columns=["ts", "open", "high", "low", "close", "volume"])
        date_expr = (pl.col("ts").dt.date() if timeframe == "1D" else pl.col("ts"))
        out.append(df.select(pl.lit(sym).alias("symbol"), date_expr.alias("date"),
                             *[pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume")]))
        used.append(sym)
        keys.append(f"{sym}:{r['snapshot_hash']}")
    if missing := sorted(set(symbols or []) - set(used) - set(skipped)):
        skipped.update({s: "no reference snapshot" for s in missing})
    bars = pl.concat(out) if out else pl.DataFrame(schema=BARS_SCHEMA)
    caveats = [("no dividend data in the v1 store: signal series = split-only (adj_factor = 1); "
                "ex-dividend drops look like small down-moves to MR entries and dividends are not credited")]
    version = f"v1store-{timeframe}-" + hashlib.sha1("|".join(sorted(keys)).encode()).hexdigest()[:12]
    return StoreLoad(bars.sort(["symbol", "date"]), used, skipped, caveats, version)
