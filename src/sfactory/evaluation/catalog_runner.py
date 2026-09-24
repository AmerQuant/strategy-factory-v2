"""Run the pre-registered catalogue, screen rows, and build the combined policy (design ch. 10-11, 13).

1. Every (row, rung) configuration is a trial recorded in the registry; DSR uses the registry's total count as
   N and the cross-trial Sharpe variance within the row's family (MR / TF), so that structurally different
   families do not inflate each other's expected maximum.
2. Row p-value = 1 - PSR(daily OOS pnl vs 0); Benjamini-Hochberg across all evaluated configurations.
3. Two-path gate (design 12.1):
   - path 1 (standalone): BH pass and DSR >= dsr_min
   - path 2 (portfolio): positive expectancy, max_dd <= dd_max, and a paired bootstrap CI of
     Sharpe(combined + row) - Sharpe(combined) entirely above zero
4. Combined policy over accepted rows: WF-native greedy selection with correlation cap + inverse-vol weights.
Everything here uses dev-period OOS only; the holdout stays locked.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl

from sfactory.evaluation.ablation import aligned_daily
from sfactory.policy.catalog import taxonomy
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.portfolio.combine import combine_rows, effective_n
from sfactory.registry.repo import Registry
from sfactory.stats.core import moments, sharpe_diff_ci, sharpe_report
from sfactory.stats.multiple import benjamini_hochberg


def _sharpe_ann(x: np.ndarray) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def run_catalog(fm, cache, bars_dev, membership, rows: list[LadderConfig], rungs=("A1",),
                registry: Registry | None = None, q: float = 0.10, dsr_min: float = 0.95,
                dd_max: float = 0.35, corr_cap: float = 0.6) -> dict:
    registry = registry or Registry()
    folds = fm.dev_folds()
    configs = [replace(r, rung=g) for r in rows for g in rungs]
    results = [run_ladder(fm, cache, bars_dev, membership, c, registry) for c in configs]
    start, end = folds[0].dp, folds[-1].oos_end
    mat = aligned_daily([r.oos_trades for r in results], start, end)
    cal = pl.date_range(start, end, "1d", eager=True)
    dates = cal.filter(cal.dt.weekday() <= 5).to_numpy()
    n_trials = registry.count_trials()
    srs = np.array([moments(mat[:, i])[0] for i in range(mat.shape[1])])
    fam = np.array([c.family for c in configs])
    var_by_fam = {f: float(np.var(srs[fam == f], ddof=1)) if (fam == f).sum() > 1 else None for f in set(fam)}
    reps = [sharpe_report(mat[:, i], n_trials, var_by_fam[fam[i]]) for i in range(mat.shape[1])]
    pvals = np.array([1 - r["psr_vs_0"] for r in reps])
    bh = benjamini_hochberg(pvals, q)
    table = []
    for i, (c, r, rep) in enumerate(zip(configs, results, reps)):
        table.append({"i": i, "row": c.rid, "rung": c.rung, "n": r.stats["n"], "sharpe": _sharpe_ann(mat[:, i]),
                      "max_dd": r.stats["max_dd"], "expectancy": r.stats["expectancy"], "p": float(pvals[i]),
                      "bh": bool(bh[i]), "dsr": rep["dsr"], "path": None, **taxonomy(c)})
    path1 = [t["i"] for t in table if t["bh"] and t["dsr"] >= dsr_min]
    for i in path1:
        table[i]["path"] = "standalone"
    fs, fe = [f.dp for f in folds], [f.oos_end for f in folds]
    base = combine_rows(mat[:, path1], dates, fs, fe, corr_cap).daily if path1 else np.zeros(len(dates))
    accepted = list(path1)
    for t in sorted(table, key=lambda t: -t["sharpe"]):
        i = t["i"]
        if i in accepted or t["expectancy"] <= 0 or t["max_dd"] > dd_max:
            continue
        trial = accepted + [i]
        cand = combine_rows(mat[:, trial], dates, fs, fe, corr_cap).daily
        lo, _ = sharpe_diff_ci(cand, base, seed=i)
        if lo > 0:
            t["path"] = "portfolio"
            accepted, base = trial, cand
    combined = combine_rows(mat[:, accepted], dates, fs, fe, corr_cap) if accepted else None
    all_eq = mat.mean(axis=1)
    return {
        "table": table, "n_trials": n_trials, "accepted": accepted,
        "combined": {"sharpe": _sharpe_ann(combined.daily) if combined else 0.0,
                     "effective_n": effective_n(mat[:, accepted]) if len(accepted) > 1 else float(len(accepted)),
                     "selected_per_fold": [[accepted[j] for j in sel] for sel in combined.selected]
                     if combined else []},
        "benchmark_all_equal": _sharpe_ann(all_eq),
        "effective_n_all": effective_n(mat),
        "daily": mat, "dates": dates, "results": results,
    }
