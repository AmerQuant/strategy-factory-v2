"""Stage H (design ch. 14): pre-registered, one-shot holdout run of the frozen combined policy.

1. freeze: the accepted row configurations + combination parameters -> policy hash
2. criteria from the dev period only (block bootstrap of the combined dev-OOS daily pnl, resampled to the
   holdout length): Sharpe >= p5, max DD <= p95, and (optionally) Sharpe above the all-rows equal-weight benchmark
3. registry.register_holdout(...) BEFORE any holdout data is touched; registry.open_holdout(...) burns it
4. rows are re-run over dev + holdout folds on the full data (the only place the lock is lifted); combination
   weights stay WF-native, so holdout DPs see the dev history exactly as they would live
5. the benchmark is the equal-weight of ALL catalogue rows (as in the dev report); every row's holdout Sharpe is
   reported descriptively only (design 14.1) - decisions rest on the combined policy alone
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

import numpy as np
import polars as pl

from sfactory.evaluation.ablation import aligned_daily
from sfactory.policy.ensemble import run_row_any
from sfactory.portfolio.combine import combine_rows
from sfactory.registry.repo import Registry


def _sr_ann(x: np.ndarray) -> float:
    sd = x.std(ddof=1)
    return float(x.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0


def _max_dd(x: np.ndarray, capital: float) -> float:
    eq = capital + np.cumsum(x)
    return float(((np.maximum.accumulate(eq) - eq) / capital).max()) if len(x) else 0.0


def freeze_policy(configs: list, corr_cap: float, lookback: int, min_history: int) -> tuple[dict, str]:
    spec = {"rows": [asdict(c) for c in configs], "corr_cap": corr_cap, "lookback": lookback,
            "min_history": min_history}
    h = hashlib.sha1(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return spec, h


def dev_criteria(dev_daily: np.ndarray, holdout_days: int, capital: float = 100_000.0, n_boot: int = 2000,
                 block: int = 20, seed: int = 0, require_beat_benchmark: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    n = len(dev_daily)
    nb = math.ceil(holdout_days / block)
    srs, dds = np.empty(n_boot), np.empty(n_boot)
    for i in range(n_boot):
        idx = (rng.integers(0, n, nb)[:, None] + np.arange(block)[None, :]).ravel()[:holdout_days] % n
        x = dev_daily[idx]
        srs[i], dds[i] = _sr_ann(x), _max_dd(x, capital)
    return {"sharpe_min": float(np.percentile(srs, 5)), "max_dd_max": float(np.percentile(dds, 95)),
            "beat_benchmark": require_beat_benchmark, "holdout_days": holdout_days,
            "dev_sharpe": _sr_ann(dev_daily), "n_boot": n_boot, "block": block}


def _calendar(start, end) -> np.ndarray:
    cal = pl.date_range(start, end, "1d", eager=True)
    return cal.filter(cal.dt.weekday() <= 5).to_numpy()


def run_holdout(fm, full_cache, bars_full, membership, catalog_report: dict, registry: Registry,
                data_version: str, corr_cap: float = 0.6, lookback: int = 504, min_history: int = 120,
                capital: float = 100_000.0) -> dict:
    accepted = catalog_report["accepted"]
    configs = [catalog_report["results"][i].config for i in accepted]
    spec, phash = freeze_policy(configs, corr_cap, lookback, min_history)
    if not configs:
        return {"status": "nothing_to_test", "policy_hash": phash}
    dev_folds = fm.dev_folds()
    hold_days = len(_calendar(fm.cfg.holdout_start, fm.cfg.data_end))
    dev_combined = combine_rows(catalog_report["daily"][:, accepted], catalog_report["dates"],
                                [f.dp for f in dev_folds], [f.oos_end for f in dev_folds],
                                corr_cap, lookback, min_history).daily
    criteria = dev_criteria(dev_combined, hold_days, capital)
    registry.register_holdout(data_version, phash, criteria)
    criteria = registry.open_holdout(data_version, phash)            # burns the holdout
    folds = dev_folds + fm.holdout_folds(unlock=True)
    all_cfgs = [r.config for r in catalog_report["results"]]
    res = [run_row_any(fm, full_cache, bars_full, membership, c, folds=folds) for c in all_cfgs]
    start, end = folds[0].dp, folds[-1].oos_end
    mat_all = aligned_daily([r.oos_trades for r in res], start, end)
    mat = mat_all[:, accepted]
    dates = _calendar(start, end)
    comb = combine_rows(mat, dates, [f.dp for f in folds], [f.oos_end for f in folds], corr_cap, lookback,
                        min_history)
    h0 = int(np.searchsorted(dates, np.datetime64(fm.cfg.holdout_start)))
    hold = comb.daily[h0:]
    bench = mat_all[h0:].mean(axis=1)
    out = {"sharpe": _sr_ann(hold), "max_dd": _max_dd(hold, capital), "benchmark_sharpe": _sr_ann(bench),
           "per_row_sharpe_descriptive": {c.rid: _sr_ann(mat_all[h0:, j]) for j, c in enumerate(all_cfgs)},
           "holdout_weights": [{configs[i].rid: w for i, w in wf.items()} for wf in comb.weights[len(dev_folds):]]}
    checks = {"sharpe": out["sharpe"] >= criteria["sharpe_min"], "max_dd": out["max_dd"] <= criteria["max_dd_max"]}
    if criteria["beat_benchmark"]:
        checks["benchmark"] = out["sharpe"] > out["benchmark_sharpe"]
    result = {"status": "pass" if all(checks.values()) else "fail", "checks": checks, "criteria": criteria,
              "holdout": out, "policy_hash": phash, "policy": spec}
    registry.record_holdout_result(data_version, result)
    return result
