"""Statistical validation (design 10.4): PSR, expected max Sharpe, DSR, MinTRL, bootstrap.

Sharpe ratios here are per-period (not annualised). `kurt` is the plain kurtosis (normal = 3).
References: Bailey & Lopez de Prado (2012, 2014).
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329


def moments(x: np.ndarray) -> tuple[float, float, float, int]:
    x = np.asarray(x, float)
    n = len(x)
    mu, sd = x.mean(), x.std(ddof=1)
    z = (x - mu) / x.std(ddof=0)
    return float(mu / sd) if sd > 0 else 0.0, float((z ** 3).mean()), float((z ** 4).mean()), n


def _sr_se_factor(sr: float, skew: float, kurt: float) -> float:
    v = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    return math.sqrt(max(v, 1e-12))


def probabilistic_sharpe(sr: float, sr_star: float, n: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    return _N.cdf((sr - sr_star) * math.sqrt(n - 1) / _sr_se_factor(sr, skew, kurt))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    if n_trials <= 1:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(sr: float, n: int, skew: float, kurt: float, n_trials: int, sr_variance: float) -> float:
    return probabilistic_sharpe(sr, expected_max_sharpe(n_trials, sr_variance), n, skew, kurt)


def min_track_record_length(sr: float, sr_star: float, skew: float = 0.0, kurt: float = 3.0,
                            alpha: float = 0.05) -> float:
    if sr <= sr_star:
        return math.inf
    z = _N.inv_cdf(1 - alpha)
    return 1 + _sr_se_factor(sr, skew, kurt) ** 2 * (z / (sr - sr_star)) ** 2


def bootstrap_ci(x, stat=np.mean, n_boot: int = 2000, alpha: float = 0.05, block: int = 1, seed: int = 0):
    """Percentile CI; block > 1 uses circular block bootstrap (for autocorrelated daily series)."""
    x = np.asarray(x, float)
    n = len(x)
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    vals = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n, nb)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        vals[i] = stat(x[idx])
    return float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))


def sharpe_report(daily_pnl, n_trials: int = 1, sr_variance: float | None = None) -> dict:
    sr, sk, ku, n = moments(daily_pnl)
    var = sr_variance if sr_variance is not None else 1.0 / max(n - 1, 1)
    return {"sr_daily": sr, "sr_annual": sr * math.sqrt(252), "skew": sk, "kurt": ku, "n_days": n,
            "psr_vs_0": probabilistic_sharpe(sr, 0.0, n, sk, ku),
            "dsr": deflated_sharpe(sr, n, sk, ku, n_trials, var), "n_trials": n_trials,
            "min_trl_days": min_track_record_length(sr, 0.0, sk, ku)}


def _sr(x: np.ndarray) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / sd) if sd > 0 else 0.0


def sharpe_diff_ci(a, b, n_boot: int = 1000, alpha: float = 0.05, block: int = 20, seed: int = 0):
    """Paired circular-block bootstrap CI of Sharpe(a) - Sharpe(b) (same resampled days for both)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    rng = np.random.default_rng(seed)
    nb = math.ceil(n / block)
    vals = np.empty(n_boot)
    for i in range(n_boot):
        idx = (rng.integers(0, n, nb)[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        vals[i] = _sr(a[idx]) - _sr(b[idx])
    return float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))
