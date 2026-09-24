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


@njit(cache=True)
def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n <= period:
        return out
    s = 0.0
    for i in range(1, period + 1):
        s += max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    v = s / period
    out[period] = v
    for i in range(period + 1, n):
        tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        v = (v * (period - 1) + tr) / period
        out[i] = v
    return out


@njit(cache=True)
def sma(x: np.ndarray, period: int) -> np.ndarray:
    n = x.shape[0]
    out = np.full(n, np.nan)
    s = 0.0
    for i in range(n):
        s += x[i]
        if i >= period:
            s -= x[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


@njit(cache=True)
def rolling_pct_rank(x: np.ndarray, window: int) -> np.ndarray:
    """Percentile rank of x[i] within x[i-window+1 .. i] (causal)."""
    n = x.shape[0]
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        if np.isnan(x[i]):
            continue
        c = 0
        m = 0
        for j in range(i - window + 1, i + 1):
            if not np.isnan(x[j]):
                m += 1
                if x[j] <= x[i]:
                    c += 1
        if m > 0:
            out[i] = c / m
    return out
