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


@njit(cache=True)
def rolling_max_prev(x: np.ndarray, window: int) -> np.ndarray:
    """max(x[i-window .. i-1]) (excludes the current bar)."""
    n = x.shape[0]
    out = np.full(n, np.nan)
    for i in range(window, n):
        m = x[i - window]
        for j in range(i - window + 1, i):
            m = max(m, x[j])
        out[i] = m
    return out


@njit(cache=True)
def rolling_min_prev(x: np.ndarray, window: int) -> np.ndarray:
    n = x.shape[0]
    out = np.full(n, np.nan)
    for i in range(window, n):
        m = x[i - window]
        for j in range(i - window + 1, i):
            m = min(m, x[j])
        out[i] = m
    return out


@njit(cache=True)
def rolling_mid(high: np.ndarray, low: np.ndarray, window: int) -> np.ndarray:
    """(max high + min low) / 2 over x[i-window+1 .. i] (includes the current bar)."""
    n = high.shape[0]
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        hi = high[i]
        lo = low[i]
        for j in range(i - window + 1, i):
            hi = max(hi, high[j])
            lo = min(lo, low[j])
        out[i] = (hi + lo) / 2
    return out


@njit(cache=True)
def supertrend_dir(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, mult: float):
    """+1 up-trend / -1 down-trend / 0 undefined; classic final-band recursion (causal)."""
    n = close.shape[0]
    d = np.zeros(n)
    up = np.nan
    dn = np.nan
    for i in range(n):
        if np.isnan(atr[i]):
            continue
        mid = (high[i] + low[i]) / 2
        bu = mid - mult * atr[i]
        bd = mid + mult * atr[i]
        if np.isnan(up):
            up, dn = bu, bd
            d[i] = 1.0 if close[i] > mid else -1.0
            continue
        up = max(bu, up) if close[i - 1] > up else bu
        dn = min(bd, dn) if close[i - 1] < dn else bd
        prev = d[i - 1] if d[i - 1] != 0 else 1.0
        if prev < 0 and close[i] > dn:
            d[i] = 1.0
        elif prev > 0 and close[i] < up:
            d[i] = -1.0
        else:
            d[i] = prev
    return d
