"""Sizing ablation (roadmap package 4): fixed sizing against vol targeting, a gross exposure cap and two daily
overlays (portfolio vol target, drawdown brake), each one registry trial.

A variant is `accepted` only if the paired block-bootstrap CI of its daily Sharpe minus the fixed-sizing Sharpe
lies entirely above zero. Sizing mostly changes risk, not return, so the table also reports max drawdown, the 5%
daily CVaR and `risk_better` (lower drawdown AND lower CVaR with a Sharpe CI that does not exclude zero on the
downside) - descriptive only; it never replaces the Sharpe gate.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np

from sfactory.evaluation.ablation import aligned_daily
from sfactory.policy.ladder import CODE_VERSION, LadderConfig, run_ladder
from sfactory.portfolio.sizing import drawdown_brake, vol_target_daily
from sfactory.stats.core import sharpe_diff_ci


def _sr(x: np.ndarray) -> float:
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def _dd(x: np.ndarray, capital: float) -> float:
    eq = capital + np.cumsum(x)
    return float(((np.maximum.accumulate(np.r_[capital, eq])[1:] - eq) / capital).max()) if len(x) else 0.0


def _cvar(x: np.ndarray, q: float = 0.05) -> float:
    if len(x) == 0:
        return 0.0
    k = max(1, int(q * len(x)))
    return float(-np.sort(x)[:k].mean())


def run_sizing(fm, cache, bars_dev, membership, cfg: LadderConfig, registry=None, max_gross: float = 1.0,
               target_ann: float = 0.10, dd_limit: float = 0.15, n_boot: int = 1000, block: int = 20) -> dict:
    """target_ann / dd_limit are fractions of cfg.capital, fixed before any result is seen."""
    base = replace(cfg, sizing="fixed", max_gross=0.0)
    trade_variants = {"fixed": base, "vol": replace(base, sizing="vol"),
                      "cap": replace(base, max_gross=max_gross),
                      "vol+cap": replace(base, sizing="vol", max_gross=max_gross)}
    results = {k: run_ladder(fm, cache, bars_dev, membership, v, registry) for k, v in trade_variants.items()}
    folds = fm.dev_folds()
    start, end = folds[0].dp, folds[-1].oos_end
    names = list(trade_variants)
    mat = aligned_daily([results[k].oos_trades for k in names], start, end)
    daily = {k: mat[:, i] for i, k in enumerate(names)}
    target_daily = target_ann / np.sqrt(252) * cfg.capital
    daily["fixed+vol_target"], lev = vol_target_daily(daily["fixed"], target_daily)
    daily["fixed+dd_brake"], expo = drawdown_brake(daily["fixed"], cfg.capital, dd_limit)
    overlays = {"fixed+vol_target": {"target_ann": target_ann, "mean_leverage": float(lev.mean())},
                "fixed+dd_brake": {"dd_limit": dd_limit, "share_braked": float((expo < 1).mean())}}
    table = []
    b = daily["fixed"]
    for i, (k, x) in enumerate(daily.items()):
        ci = None if k == "fixed" else sharpe_diff_ci(x, b, n_boot=n_boot, block=block, seed=i)
        row = {"variant": k, "sharpe": _sr(x), "max_dd": _dd(x, cfg.capital), "cvar5": _cvar(x),
               "net": float(x.sum()), "sharpe_diff_ci": ci, "accepted": bool(ci is not None and ci[0] > 0)}
        row["risk_better"] = bool(ci is not None and row["max_dd"] < _dd(b, cfg.capital)
                                  and row["cvar5"] < _cvar(b) and ci[1] > 0)
        if k in results:
            row["n"] = results[k].stats["n"]
        row.update(overlays.get(k, {}))
        table.append(row)
        if registry is not None and k in overlays:
            registry.record_trial(f"{base.rid}|{k}", {**asdict(base), "overlay": {k: overlays[k]}},
                                  cache.data_version, CODE_VERSION,
                                  {"sharpe": row["sharpe"], "max_dd": row["max_dd"], "n": len(x)})
    return {"row": base.rid, "table": table, "accepted": [r["variant"] for r in table if r["accepted"]],
            "daily": daily, "results": results}
