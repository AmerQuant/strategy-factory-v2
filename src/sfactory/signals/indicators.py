"""Causal indicators (value at t uses data <= t only)."""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def rsi_wilder(close: np.ndarray, period: int) -> np.ndarray:
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n <= period:
        return out
    gain = 0.0
    loss = 0.0
    for i in range(1, period + 1):
        ch = close[i] - close[i - 1]
        gain += max(ch, 0.0)
        loss += max(-ch, 0.0)
    gain /= period
    loss /= period
    out[period] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
    for i in range(period + 1, n):
        ch = close[i] - close[i - 1]
        gain = (gain * (period - 1) + max(ch, 0.0)) / period
        loss = (loss * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
    return out
