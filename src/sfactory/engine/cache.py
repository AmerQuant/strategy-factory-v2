"""Compute-once / slice-many: per-symbol arrays + trade cache keyed by (rule, params, data_version)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from sfactory.engine.cell import run_cell
from sfactory.signals.indicators import rsi_wilder


@dataclass
class SymbolArrays:
    symbol: str
    dates: np.ndarray
    sig_close: np.ndarray
    sig_high: np.ndarray
    ex_open: np.ndarray
    ex_close: np.ndarray
    div: np.ndarray


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
                                g["open"].to_numpy(), g["close"].to_numpy(), div)
    return out


class TradeCache:
    def __init__(self, arrays: dict[str, SymbolArrays], data_version: str, notional: float = 100_000.0,
                 cost_bps: float = 5.0):
        self.arrays, self.data_version = arrays, data_version
        self.notional, self.cost_bps = notional, cost_bps
        self._store: dict[tuple, pl.DataFrame] = {}
        self.computed = 0

    def rsi_mr(self, symbol: str, period: int, threshold: float, direction: int = 1, max_hold: int = 5):
        key = ("rsi_mr", symbol, period, threshold, direction, max_hold, self.data_version, self.cost_bps)
        if key not in self._store:
            a = self.arrays[symbol]
            rsi = rsi_wilder(a.sig_close, period)
            entry = (rsi < threshold) if direction == 1 else (rsi > 100 - threshold)
            entry = np.where(np.isnan(rsi), False, entry)
            rec = run_cell(entry, a.sig_close, a.sig_high, a.ex_open, a.ex_close, a.div,
                           direction, max_hold, self.notional, self.cost_bps)
            self._store[key] = _to_frame(symbol, a.dates, rec)
            self.computed += 1
        return self._store[key]


def _to_frame(symbol: str, dates: np.ndarray, rec: np.ndarray) -> pl.DataFrame:
    idx = rec[:, :3].astype(np.int64) if len(rec) else np.zeros((0, 3), np.int64)
    return pl.DataFrame({
        "symbol": [symbol] * len(rec),
        "signal_date": dates[idx[:, 0]], "entry_date": dates[idx[:, 1]], "exit_date": dates[idx[:, 2]],
        "entry_px": rec[:, 3], "exit_px": rec[:, 4], "shares": rec[:, 5], "gross_pnl": rec[:, 6],
        "cost": rec[:, 7], "dividends": rec[:, 8], "net_pnl": rec[:, 9], "forced_exit": rec[:, 10] > 0,
    }, schema_overrides={"signal_date": pl.Date, "entry_date": pl.Date, "exit_date": pl.Date})
