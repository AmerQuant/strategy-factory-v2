"""Rule specifications (hashable, part of the trade-cache key) and their causal array builders.

Exit library (MR, design 7.4): prev_high (neutral), time, rsi_above, with optional ATR target / stop.
Filter library (design 7.5): above_ma, vol_below (ATR% percentile), and the structural `market_up`
(market proxy above its MA; fixed, never selected in-fold - design ch. 12).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sfactory.signals.indicators import atr_wilder, rolling_pct_rank, rsi_wilder, sma


@dataclass(frozen=True)
class ExitSpec:
    kind: str = "prev_high"      # prev_high | time | rsi_above
    param: float = 0.0           # time: bars ; rsi_above: level
    max_hold: int = 5
    target_atr: float = 0.0
    stop_atr: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.param:g}:h{self.max_hold}:t{self.target_atr:g}:s{self.stop_atr:g}"


NEUTRAL_MR_EXIT = ExitSpec("prev_high", 0, 5)

MR_EXIT_LIBRARY = (
    NEUTRAL_MR_EXIT,
    ExitSpec("time", 3, 3),
    ExitSpec("time", 5, 5),
    ExitSpec("rsi_above", 50, 10),
    ExitSpec("rsi_above", 70, 10),
    ExitSpec("prev_high", 0, 5, target_atr=1.0),
    ExitSpec("prev_high", 0, 10, stop_atr=3.0),
)


@dataclass(frozen=True)
class FilterSpec:
    kind: str                    # above_ma | vol_below | market_up
    param: float = 0.0

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.param:g}"


MR_FILTER_LIBRARY = (FilterSpec("above_ma", 200), FilterSpec("vol_below", 0.8))
STRUCTURAL_MARKET_UP = FilterSpec("market_up", 200)


def exit_signal(spec: ExitSpec, sig_close: np.ndarray, sig_high: np.ndarray, direction: int,
                rsi_period: int = 2) -> np.ndarray:
    n = len(sig_close)
    out = np.zeros(n, dtype=np.bool_)
    if spec.kind == "prev_high":
        if direction == 1:
            out[1:] = sig_close[1:] > sig_high[:-1]
        else:
            out[1:] = sig_close[1:] < sig_high[:-1]
    elif spec.kind == "time":
        pass  # handled by max_hold
    elif spec.kind == "rsi_above":
        r = rsi_wilder(sig_close, rsi_period)
        out = np.where(np.isnan(r), False, (r > spec.param) if direction == 1 else (r < 100 - spec.param))
    else:
        raise ValueError(spec.kind)
    return out


def atr_exec_units(sig_high, sig_low, sig_close, factor, period: int = 14) -> np.ndarray:
    """ATR on the adjusted series divided by the adjustment factor at t: causal and in execution units."""
    return atr_wilder(sig_high, sig_low, sig_close, period) / factor


def filter_mask(spec: FilterSpec, sig_close: np.ndarray, atr_exec: np.ndarray, ex_close: np.ndarray,
                market_flag: np.ndarray | None) -> np.ndarray:
    if spec.kind == "above_ma":
        m = sma(sig_close, int(spec.param))
        return np.where(np.isnan(m), False, sig_close > m)
    if spec.kind == "vol_below":
        pr = rolling_pct_rank(atr_exec / ex_close, 252)
        return np.where(np.isnan(pr), False, pr <= spec.param)
    if spec.kind == "market_up":
        if market_flag is None:
            raise ValueError("market_up needs a market regime series (TradeCache.set_market_regime)")
        return market_flag
    raise ValueError(spec.kind)
