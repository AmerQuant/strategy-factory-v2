"""WF-native combination of rows (design ch. 11 / 13): at every DP, weights use only daily pnl before the DP.

Selection per DP: rank candidate rows by trailing Sharpe, add greedily if trailing correlation with every
already-selected row is <= corr_cap; weights are inverse trailing volatility (risk parity-lite), sum to 1.
Folds without enough history fall back to equal weight over all candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Combined:
    daily: np.ndarray
    weights: list = field(default_factory=list)     # per fold: {row_index: weight}
    selected: list = field(default_factory=list)    # per fold: [row_index]


def effective_n(daily: np.ndarray) -> float:
    """Effective number of independent bets: (sum lambda)^2 / sum lambda^2 of the correlation matrix."""
    x = np.asarray(daily, float)
    keep = x.std(axis=0) > 0
    if keep.sum() < 2:
        return float(keep.sum())
    lam = np.clip(np.linalg.eigvalsh(np.corrcoef(x[:, keep], rowvar=False)), 0, None)
    return float(lam.sum() ** 2 / (lam ** 2).sum())


def combine_rows(daily: np.ndarray, dates: np.ndarray, fold_starts: list, fold_ends: list,
                 corr_cap: float = 0.6, lookback: int = 504, min_history: int = 120) -> Combined:
    """daily: T x N aligned zero-filled daily pnl of the candidate rows on calendar `dates`."""
    t, n = daily.shape
    out = np.zeros(t)
    res = Combined(out)
    for s, e in zip(fold_starts, fold_ends):
        i0 = int(np.searchsorted(dates, np.datetime64(s)))
        i1 = int(np.searchsorted(dates, np.datetime64(e)))
        hist = daily[max(0, i0 - lookback):i0]
        if len(hist) < min_history:
            sel = list(range(n))
            w = {i: 1.0 / n for i in sel}
        else:
            sd = hist.std(axis=0, ddof=1)
            sr = np.where(sd > 0, hist.mean(axis=0) / np.where(sd > 0, sd, 1), -np.inf)
            corr = np.corrcoef(hist, rowvar=False) if n > 1 else np.ones((1, 1))
            sel = []
            for i in np.argsort(-sr):
                if not np.isfinite(sr[i]) or sr[i] <= 0:
                    continue
                if all(np.nan_to_num(corr[i, j]) <= corr_cap for j in sel):
                    sel.append(int(i))
            inv = {i: 1.0 / sd[i] for i in sel if sd[i] > 0}
            tot = sum(inv.values())
            w = {i: v / tot for i, v in inv.items()} if tot > 0 else {}
        for i, wi in w.items():
            out[i0:i1] += wi * daily[i0:i1, i]
        res.weights.append(w)
        res.selected.append(sorted(w))
    return res


def family_ensemble(daily: np.ndarray, members: list[int]) -> np.ndarray:
    """Ensemble row (design 13.3): equal-weight average of the members' daily pnl."""
    return daily[:, members].mean(axis=1) if members else np.zeros(daily.shape[0])
