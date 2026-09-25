"""WF-native combination of rows (design ch. 11 / 13): at every DP, weights use only daily pnl before the DP.

Selection per DP: rank candidate rows by trailing Sharpe, add greedily if trailing correlation with every
already-selected row is <= corr_cap; weights are inverse trailing volatility (risk parity-lite), sum to 1.
Folds without enough history fall back to equal weight over all candidates.

Optional risk budget (`RiskBudget`, all from the same trailing window, i.e. data before the DP):
- `max_weight`: cap per row; the excess goes pro rata to the rows below the cap (water filling);
- `family_cap`: cap per family (MR / TF / VOL / XS / CAL / EV); excess to the other families pro rata;
  if every row or family is capped the rest stays in cash (weights sum below 1);
- `target_vol_ann`: scale all weights so the ex-ante portfolio volatility (trailing covariance) is the target
  (fraction of `capital` per year), leverage clipped to [0, lev_max]. Without enough history: leverage 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Combined:
    daily: np.ndarray
    weights: list = field(default_factory=list)     # per fold: {row_index: weight}
    selected: list = field(default_factory=list)    # per fold: [row_index]
    leverage: list = field(default_factory=list)    # per fold: risk-budget leverage (1.0 without a vol target)


@dataclass(frozen=True)
class RiskBudget:
    max_weight: float | None = None
    family_cap: float | None = None
    target_vol_ann: float | None = None
    capital: float = 100_000.0
    lev_max: float = 2.0

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.max_weight, self.family_cap, self.target_vol_ann))


def cap_weights(w: dict, cap: float | None = None, groups: dict | None = None,
                group_cap: float | None = None, iters: int = 100) -> dict:
    """Water filling under a per-key cap and / or a per-group cap (`groups`: key -> group). Violations are cut
    to the cap and the excess is spread pro rata over the keys that still have room under both caps; repeated
    until nothing is violated. What cannot be placed stays unallocated (cash)."""
    out = {k: float(v) for k, v in w.items()}
    groups = groups or {}
    for _ in range(iters):
        excess = 0.0
        if cap is not None:
            for k, v in out.items():
                if v > cap:
                    excess += v - cap
                    out[k] = cap
        tot: dict = {}
        if group_cap is not None:
            for k, v in out.items():
                tot[groups[k]] = tot.get(groups[k], 0.0) + v
            for g, t in tot.items():
                if t > group_cap:
                    f = group_cap / t
                    for k, v in list(out.items()):
                        if groups[k] == g:
                            excess += v * (1 - f)
                            out[k] = v * f
                    tot[g] = group_cap
        if excess < 1e-12:
            break
        free = [k for k, v in out.items() if (cap is None or v < cap - 1e-12)
                and (group_cap is None or tot.get(groups[k], 0.0) < group_cap - 1e-12)]
        room = sum(out[k] for k in free)
        if room <= 0:
            break
        for k in free:
            out[k] += excess * out[k] / room
    return out


def apply_budget(w: dict, hist: np.ndarray | None, budget: RiskBudget, families: list | None) -> tuple[dict, float]:
    """(weights after the caps, leverage from the vol target). `hist` columns are indexed by the keys of w."""
    fam_cap = budget.family_cap if families is not None else None
    if budget.max_weight is not None or fam_cap is not None:
        w = cap_weights(w, budget.max_weight, {k: families[k] for k in w} if families is not None else None, fam_cap)
    lev = 1.0
    if budget.target_vol_ann is not None and hist is not None and len(hist) > 1 and w:
        keys = sorted(w)
        cov = np.atleast_2d(np.cov(hist[:, keys], rowvar=False))
        vec = np.array([w[k] for k in keys])
        sd = float(np.sqrt(max(vec @ cov @ vec, 0.0)))
        target = budget.target_vol_ann * budget.capital / np.sqrt(252)
        lev = float(np.clip(target / sd, 0.0, budget.lev_max)) if sd > 0 else budget.lev_max
    return w, lev


def effective_n(daily: np.ndarray) -> float:
    """Effective number of independent bets: (sum lambda)^2 / sum lambda^2 of the correlation matrix."""
    x = np.asarray(daily, float)
    keep = x.std(axis=0) > 0
    if keep.sum() < 2:
        return float(keep.sum())
    lam = np.clip(np.linalg.eigvalsh(np.corrcoef(x[:, keep], rowvar=False)), 0, None)
    return float(lam.sum() ** 2 / (lam ** 2).sum())


def combine_rows(daily: np.ndarray, dates: np.ndarray, fold_starts: list, fold_ends: list,
                 corr_cap: float = 0.6, lookback: int = 504, min_history: int = 120,
                 budget: RiskBudget | None = None, families: list | None = None) -> Combined:
    """daily: T x N aligned zero-filled daily pnl of the candidate rows on calendar `dates`.
    families: family label per column (for `RiskBudget.family_cap`)."""
    t, n = daily.shape
    out = np.zeros(t)
    res = Combined(out)
    for s, e in zip(fold_starts, fold_ends):
        i0 = int(np.searchsorted(dates, np.datetime64(s)))
        i1 = int(np.searchsorted(dates, np.datetime64(e)))
        hist = daily[max(0, i0 - lookback):i0]
        enough = len(hist) >= min_history
        if not enough:
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
        lev = 1.0
        if budget is not None and budget.active:
            w, lev = apply_budget(w, hist if enough else None, budget, families)
        for i, wi in w.items():
            out[i0:i1] += lev * wi * daily[i0:i1, i]
        res.weights.append({i: lev * wi for i, wi in w.items()})
        res.selected.append(sorted(w))
        res.leverage.append(lev)
    return res


def family_ensemble(daily: np.ndarray, members: list[int]) -> np.ndarray:
    """Ensemble row (design 13.3): equal-weight average of the members' daily pnl."""
    return daily[:, members].mean(axis=1) if members else np.zeros(daily.shape[0])
