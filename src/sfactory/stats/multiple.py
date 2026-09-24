"""Multiple-testing tools (design 10.4 / ch. 11): Benjamini-Hochberg FDR and PBO via CSCV.

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
