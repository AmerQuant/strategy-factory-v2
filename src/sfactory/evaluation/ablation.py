"""Ablation ladder report (design 9.2) with DSR and PBO across the evaluated configurations.

Each rung is a separate evaluated configuration (a trial). A rung is 'accepted' only if its daily OOS pnl beats
the previous accepted rung with a block-bootstrap 95% CI of the mean difference entirely above zero (a Sharpe
that merely moves from -0.4 to -0.1 is noise, not an improvement). PBO/CSCV runs over all rungs' daily pnl.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl

from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.stats.core import bootstrap_ci, sharpe_report
from sfactory.stats.multiple import pbo_cscv


def aligned_daily(trade_sets: list[pl.DataFrame], start, end) -> np.ndarray:
    cal = pl.DataFrame({"exit_date": pl.date_range(start, end, "1d", eager=True)}).filter(
        pl.col("exit_date").dt.weekday() <= 5)
    cols = []
    for t in trade_sets:
        if len(t) == 0:
            cols.append(np.zeros(len(cal)))
            continue
        agg = t.group_by("exit_date").agg(pl.col("net_pnl").sum())
        cols.append(cal.join(agg, on="exit_date", how="left").sort("exit_date")["net_pnl"].fill_null(0.0).to_numpy())
    return np.column_stack(cols)


def run_ablation(fm, cache, bars_dev, membership, base: LadderConfig, rungs=("A0", "A1", "A3", "A4"),
                 registry=None, n_blocks: int = 10, block: int = 20) -> dict:
    folds = fm.dev_folds()
    results = {r: run_ladder(fm, cache, bars_dev, membership, replace(base, rung=r), registry) for r in rungs}
    mat = aligned_daily([results[r].oos_trades for r in rungs], folds[0].dp, folds[-1].oos_end)
    n_trials = len(rungs)
    sr = [sharpe_report(mat[:, i], n_trials) for i in range(len(rungs))]
    var = float(np.var([s["sr_daily"] for s in sr], ddof=1)) if len(rungs) > 1 else None
    table, best = [], None
    for i, r in enumerate(rungs):
        rep = sharpe_report(mat[:, i], n_trials, var)
        if best is None:
            accepted, ci = True, None
        else:
            ci = bootstrap_ci(mat[:, i] - mat[:, best], n_boot=1000, block=block, seed=i)
            accepted = ci[0] > 0
        if accepted:
            best = i
        table.append({"rung": r, "n": results[r].stats["n"], "sharpe": results[r].stats["sharpe"],
                      "max_dd": results[r].stats["max_dd"], "dsr": rep["dsr"], "diff_ci": ci,
                      "accepted": accepted, "active": rungs[best]})
    return {"table": table, "pbo": pbo_cscv(mat, n_blocks), "results": results}
