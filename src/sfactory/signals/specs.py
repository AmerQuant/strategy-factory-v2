"""Rule specifications (hashable, part of the trade-cache key) and their causal array builders.

Exit libraries (design 7.4): MR - prev_high (neutral), time, rsi_above, optional ATR target / stop;
TF - reverse (neutral, the method's opposite state), ATR trailing stop, time.
Filter library (design 7.5): above_ma, vol_below (ATR% percentile), and the structural `market_up`
(market proxy above its MA; fixed, never selected in-fold - design ch. 12).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sfactory.signals.indicators import atr_wilder, rolling_pct_rank, rsi_wilder, sma


@dataclass(frozen=True)
class ExitSpec:
    kind: str = "prev_high"      # prev_high | time | rsi_above | reverse | none
    param: float = 0.0           # time: bars ; rsi_above: level
    max_hold: int = 5
    target_atr: float = 0.0
    stop_atr: float = 0.0
    trail_atr: float = 0.0

    @property
    def id(self) -> str:
        return (f"{self.kind}:{self.param:g}:h{self.max_hold}:t{self.target_atr:g}:s{self.stop_atr:g}"
                f":tr{self.trail_atr:g}")


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

NEUTRAL_TF_EXIT = ExitSpec("reverse", 0, 250)

TF_EXIT_LIBRARY = (
    NEUTRAL_TF_EXIT,
    ExitSpec("none", 0, 250, trail_atr=3.0),
    ExitSpec("none", 0, 250, trail_atr=2.0),
    ExitSpec("reverse", 0, 250, stop_atr=2.0),
    ExitSpec("time", 20, 20),
    ExitSpec("time", 40, 40),
)


def neutral_exit(family: str) -> ExitSpec:
    return NEUTRAL_MR_EXIT if family == "MR" else NEUTRAL_TF_EXIT


def exit_library(family: str) -> tuple:
    return MR_EXIT_LIBRARY if family == "MR" else TF_EXIT_LIBRARY


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
    elif spec.kind in ("time", "none"):
        pass  # handled by max_hold / trailing stop
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
                market_flag: np.ndarray | None, direction: int = 1) -> np.ndarray:
    """Trend-side filters are mirrored for sells (above_ma -> below; market_up -> market down)."""
    if spec.kind == "above_ma":
        m = sma(sig_close, int(spec.param))
        return np.where(np.isnan(m), False, (sig_close > m) if direction == 1 else (sig_close < m))
    if spec.kind == "vol_below":
        pr = rolling_pct_rank(atr_exec / ex_close, 252)
        return np.where(np.isnan(pr), False, pr <= spec.param)
    if spec.kind == "market_up":
        if market_flag is None:
            raise ValueError("market_up needs a market regime series (TradeCache.set_market_regime)")
        return market_flag if direction == 1 else ~market_flag
    raise ValueError(spec.kind)
