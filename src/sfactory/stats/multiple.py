"""Multiple-testing tools (design 10.4 / ch. 11): Benjamini-Hochberg FDR, PBO via CSCV, Hansen's SPA.

PBO follows Bailey, Borwein, Lopez de Prado & Zhu (2014): split T periods into S blocks; for every choice of
S/2 blocks as IS, pick the best configuration by IS Sharpe and record the logit of its relative OOS rank.
PBO = share of splits where that configuration ranks at or below the OOS median.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


def benjamini_hochberg(pvals, q: float = 0.05) -> np.ndarray:
    p = np.asarray(pvals, float)
    m = len(p)
    order = np.argsort(p)
    thresh = q * (np.arange(1, m + 1) / m)
    passed = p[order] <= thresh
    k = np.max(np.nonzero(passed)[0]) + 1 if passed.any() else 0
    out = np.zeros(m, dtype=bool)
    out[order[:k]] = True
    return out


def _sharpe_cols(x: np.ndarray) -> np.ndarray:
    sd = x.std(axis=0, ddof=1)
    return np.where(sd > 0, x.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0)


def pbo_cscv(returns: np.ndarray, n_blocks: int = 10) -> dict:
    """returns: T x N matrix (periods x configurations), e.g. zero-filled daily OOS pnl per configuration."""
    r = np.asarray(returns, float)
    t, n = r.shape
    if n < 2 or n_blocks % 2 or t < n_blocks * 2:
        raise ValueError("need N >= 2 configs, even n_blocks, and T >= 2 * n_blocks")
    blocks = np.array_split(np.arange(t), n_blocks)
    lambdas = []
    for is_ids in combinations(range(n_blocks), n_blocks // 2):
        is_rows = np.concatenate([blocks[i] for i in is_ids])
        oos_rows = np.concatenate([blocks[i] for i in range(n_blocks) if i not in is_ids])
        best = int(np.argmax(_sharpe_cols(r[is_rows])))
        oos = _sharpe_cols(r[oos_rows])
        w = (1 + np.sum(oos < oos[best])) / (n + 1)
        lambdas.append(np.log(w / (1 - w)))
    lam = np.array(lambdas)
    return {"pbo": float((lam <= 0).mean()), "n_splits": len(lam), "median_logit": float(np.median(lam))}


def _stationary_indices(n: int, mean_block: float, rng) -> np.ndarray:
    idx = np.empty(n, dtype=np.int64)
    p = 1.0 / mean_block
    idx[0] = rng.integers(0, n)
    for t in range(1, n):
        idx[t] = rng.integers(0, n) if rng.random() < p else (idx[t - 1] + 1) % n
    return idx


def spa_test(candidates: np.ndarray, benchmark: np.ndarray, n_boot: int = 1000, mean_block: float = 20.0,
             seed: int = 0) -> dict:
    """Hansen (2005) Superior Predictive Ability test (consistent version) on performance differentials.

    candidates: T x K daily returns of all evaluated strategies; benchmark: T returns.
    H0: no candidate beats the benchmark. Small p-value = at least one genuinely better candidate.
    """
    x = np.asarray(candidates, float)
    if x.ndim == 1:
        x = x[:, None]
    d = x - np.asarray(benchmark, float)[:, None]
    n, k = d.shape
    rng = np.random.default_rng(seed)
    dbar = d.mean(axis=0)
    boots = np.empty((n_boot, k))
    for b in range(n_boot):
        boots[b] = d[_stationary_indices(n, mean_block, rng)].mean(axis=0)
    omega = np.sqrt(n) * boots.std(axis=0, ddof=1)
    omega = np.where(omega > 0, omega, np.inf)
    t_obs = max(0.0, float(np.max(np.sqrt(n) * dbar / omega)))
    thresh = -np.sqrt(2 * np.log(np.log(n)))
    mu_c = np.where(np.sqrt(n) * dbar / omega >= thresh, dbar, 0.0)
    t_b = np.maximum(0.0, np.max(np.sqrt(n) * (boots - mu_c) / omega, axis=1))
    return {"p_value": float((t_b >= t_obs).mean()), "stat": t_obs, "best": int(np.argmax(dbar)), "n_models": k}
