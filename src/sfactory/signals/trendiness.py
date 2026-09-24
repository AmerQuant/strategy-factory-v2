"""Causal "trendiness" features of a symbol's market (design 12.5, mechanism 3).

Every value at bar t uses the adjusted signal close up to and including t only. All three are
scale-invariant (ratios of the same series), as the backward dividend adjustment requires.

- efficiency: Kaufman efficiency ratio |c_t - c_{t-n}| / sum |c_i - c_{i-1}| over n bars (1 = straight line)
- variance_ratio: Lo-MacKinlay VR(q) of log returns over the last n bars (> 1 trending, < 1 mean reverting)
- autocorr: lag-1 autocorrelation of log returns over the last n bars
"""
from __future__ import annotations

import numpy as np
from numba import njit

FEATURES = ("efficiency", "variance_ratio", "autocorr")


@njit(cache=True)
def efficiency_ratio(c: np.ndarray, n: int) -> np.ndarray:
    m = c.shape[0]
    out = np.full(m, np.nan)
    for t in range(n, m):
        path = 0.0
        for i in range(t - n + 1, t + 1):
            path += abs(c[i] - c[i - 1])
        out[t] = abs(c[t] - c[t - n]) / path if path > 0 else 0.0
    return out


@njit(cache=True)
def variance_ratio(c: np.ndarray, n: int, q: int = 5) -> np.ndarray:
    m = c.shape[0]
    out = np.full(m, np.nan)
    r = np.full(m, np.nan)
    for t in range(1, m):
        r[t] = np.log(c[t] / c[t - 1])
    for t in range(n, m):
        mu = 0.0
        for i in range(t - n + 1, t + 1):
            mu += r[i]
        mu /= n
        v1 = 0.0
        for i in range(t - n + 1, t + 1):
            v1 += (r[i] - mu) ** 2
        v1 /= n - 1
        vq = 0.0
        k = 0
        for i in range(t - n + q, t + 1):
            s = 0.0
            for j in range(i - q + 1, i + 1):
                s += r[j]
            vq += (s - q * mu) ** 2
            k += 1
        if k > 1 and v1 > 0:
            out[t] = (vq / (k - 1)) / (q * v1)
    return out


@njit(cache=True)
def autocorr1(c: np.ndarray, n: int) -> np.ndarray:
    m = c.shape[0]
    out = np.full(m, np.nan)
    r = np.full(m, np.nan)
    for t in range(1, m):
        r[t] = np.log(c[t] / c[t - 1])
    for t in range(n + 1, m):
        mu = 0.0
        for i in range(t - n + 1, t + 1):
            mu += r[i]
        mu /= n
        num = 0.0
        den = 0.0
        for i in range(t - n + 1, t + 1):
            den += (r[i] - mu) ** 2
            if i > t - n + 1:
                num += (r[i] - mu) * (r[i - 1] - mu)
        out[t] = num / den if den > 0 else 0.0
    return out


def feature_series(kind: str, sig_close: np.ndarray, window: int) -> np.ndarray:
    c = np.asarray(sig_close, float)
    if kind == "efficiency":
        return efficiency_ratio(c, window)
    if kind == "variance_ratio":
        return variance_ratio(c, window)
    if kind == "autocorr":
        return autocorr1(c, window)
    raise ValueError(kind)
