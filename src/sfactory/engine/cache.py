"""Compute-once / slice-many: per-symbol arrays + trade cache keyed by (rule, params, data_version)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from sfactory.costs.model import CostModel
from sfactory.engine.cell import run_cell
from sfactory.engine.generic import run_cell_generic
from sfactory.signals.indicators import rsi_wilder
from sfactory.signals.methods import EntrySpec, entry_and_score, reverse_exit
from sfactory.signals.specs import (
    NEUTRAL_MR_EXIT,
    ExitSpec,
    FilterSpec,
    atr_exec_units,
    exit_signal,
    filter_mask,
)


@dataclass
class SymbolArrays:
    symbol: str
    dates: np.ndarray
    sig_close: np.ndarray
    sig_high: np.ndarray
    ex_open: np.ndarray
    ex_close: np.ndarray
    div: np.ndarray
    sig_low: np.ndarray | None = None
    factor: np.ndarray | None = None


def prepare_arrays(bars: pl.DataFrame, dividends: pl.DataFrame) -> dict[str, SymbolArrays]:
    """bars must contain adj_factor (see data.adjust)."""
    out = {}
    for (sym,), g in bars.sort(["symbol", "date"]).partition_by("symbol", as_dict=True).items():
        dates = g["date"].to_numpy()
        f = g["adj_factor"].to_numpy()
        div = np.zeros(len(g))
        d = dividends.filter(pl.col("symbol") == sym)
        for ex, amt in zip(d["ex_date"].to_numpy(), d["amount"].to_numpy()):
            i = int(np.searchsorted(dates, ex))
            if i < len(dates) and dates[i] == ex:
                div[i] = amt
        out[sym] = SymbolArrays(sym, dates, g["close"].to_numpy() * f, g["high"].to_numpy() * f,
                                g["open"].to_numpy(), g["close"].to_numpy(), div,
                                g["low"].to_numpy() * f, f)
    return out


class TradeCache:
    """In-memory cache, optionally backed by Parquet files in `cache_dir` (one file per key)."""

    def __init__(self, arrays: dict[str, SymbolArrays], data_version: str, notional: float = 100_000.0,
                 cost_bps: float = 5.0, cache_dir: str | Path | None = None, cost_model: CostModel | None = None):
        self.arrays, self.data_version = arrays, data_version
        self.notional = notional
        self.cost_model = cost_model if cost_model is not None else CostModel.flat(cost_bps)
        self._store: dict[tuple, pl.DataFrame] = {}
        self.computed = 0
        self.loaded = 0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._regime_dates: np.ndarray | None = None
        self._regime_flags: np.ndarray | None = None
        self._regime_id = "none"

    def set_market_regime(self, dates: np.ndarray, flags: np.ndarray, regime_id: str) -> None:
        """Causal market regime series (e.g. market proxy above its MA) used by structural filters."""
        order = np.argsort(dates)
        self._regime_dates, self._regime_flags = np.asarray(dates)[order], np.asarray(flags, bool)[order]
        self._regime_id = regime_id

    def _regime_for(self, dates: np.ndarray) -> np.ndarray | None:
        if self._regime_dates is None:
            return None
        idx = np.searchsorted(self._regime_dates, dates)
        idx = np.clip(idx, 0, len(self._regime_dates) - 1)
        return np.where(self._regime_dates[idx] == dates, self._regime_flags[idx], False)

    def _path(self, key: tuple) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(repr(key + (self.notional,)).encode()).hexdigest()[:20]
        return self.cache_dir / f"{key[0]}-{key[1]}-{h}.parquet"

    def _get(self, key: tuple, compute) -> pl.DataFrame:
        if key in self._store:
            return self._store[key]
        path = self._path(key)
        if path is not None and path.exists():
            df = pl.read_parquet(path)
            self.loaded += 1
        else:
            df = compute()
            self.computed += 1
            if path is not None:
                df.write_parquet(path)
        self._store[key] = df
        return df

    def rsi_mr(self, symbol: str, period: int, threshold: float, direction: int = 1, max_hold: int = 5):
        key = ("rsi_mr", symbol, period, threshold, direction, max_hold, self.data_version,
               self.cost_model.fingerprint())

        def compute():
            a = self.arrays[symbol]
            rsi = rsi_wilder(a.sig_close, period)
            entry = (rsi < threshold) if direction == 1 else (rsi > 100 - threshold)
            entry = np.where(np.isnan(rsi), False, entry)
            rec = run_cell(entry, a.sig_close, a.sig_high, a.ex_open, a.ex_close, a.div,
                           direction, max_hold, self.notional, self.cost_model.per_side_bps(symbol))
            return _to_frame(symbol, a.dates, rec, rsi)

        return self._get(key, compute)

    def mr_trades(self, symbol: str, period: int, threshold: float, direction: int = 1,
                  exit_spec: ExitSpec = NEUTRAL_MR_EXIT, filters: tuple[FilterSpec, ...] = ()) -> pl.DataFrame:
        """RSI(2) mean-reversion entry (kept for the ladder API); `period` is fixed at 2."""
        return self.trades(symbol, EntrySpec("rsi", threshold, direction), exit_spec, filters)

    def trades(self, symbol: str, entry_spec: EntrySpec, exit_spec: ExitSpec,
               filters: tuple[FilterSpec, ...] = ()) -> pl.DataFrame:
        """Any entry method + any exit from the libraries + any combination of filters."""
        fkey = tuple(f.id for f in filters)
        needs_regime = any(f.kind == "market_up" for f in filters)
        key = ("tr", symbol, entry_spec.id, exit_spec.id, fkey,
               self._regime_id if needs_regime else "-", self.data_version, self.cost_model.fingerprint())

        def compute():
            a = self.arrays[symbol]
            d = entry_spec.direction
            entry, score = entry_and_score(entry_spec, a.sig_high, a.sig_low, a.sig_close)
            atr = atr_exec_units(a.sig_high, a.sig_low, a.sig_close, a.factor)
            for f in filters:
                entry = entry & filter_mask(f, a.sig_close, atr, a.ex_close, self._regime_for(a.dates), d)
            if exit_spec.kind == "reverse":
                ex = reverse_exit(entry_spec, a.sig_high, a.sig_low, a.sig_close)
            else:
                ex = exit_signal(exit_spec, a.sig_close, a.sig_high if d == 1 else a.sig_low, d, 2)
            rec = run_cell_generic(entry.astype(np.bool_), ex.astype(np.bool_), a.ex_open, a.ex_close, a.div,
                                   atr, d, exit_spec.max_hold, exit_spec.target_atr, exit_spec.stop_atr,
                                   self.notional, self.cost_model.per_side_bps(symbol), exit_spec.trail_atr)
            return _to_frame(symbol, a.dates, rec, score)

        return self._get(key, compute)


def _to_frame(symbol: str, dates: np.ndarray, rec: np.ndarray, score: np.ndarray) -> pl.DataFrame:
    """`score` is the signal-strength series (e.g. RSI) sampled at the signal bar, used by daily rankers."""
    idx = rec[:, :3].astype(np.int64) if len(rec) else np.zeros((0, 3), np.int64)
    return pl.DataFrame({
        "symbol": [symbol] * len(rec),
        "signal_date": dates[idx[:, 0]], "entry_date": dates[idx[:, 1]], "exit_date": dates[idx[:, 2]],
        "entry_px": rec[:, 3], "exit_px": rec[:, 4], "shares": rec[:, 5], "gross_pnl": rec[:, 6],
        "cost": rec[:, 7], "dividends": rec[:, 8], "net_pnl": rec[:, 9], "forced_exit": rec[:, 10] > 0,
        "score": score[idx[:, 0]] if len(rec) else np.zeros(0),
    }, schema_overrides={"signal_date": pl.Date, "entry_date": pl.Date, "exit_date": pl.Date})
