"""Second-stage analysis of the accepted rows (design 12.4-12.5 and package 4), run after the catalogue.

For every accepted single row (ensembles are skipped: their members are analysed as rows of their own when
accepted), two pre-registered ablations run on dev-period OOS only:
- edge on/off (`policy.edge_state.run_edge_state`): persistence verdict + each mechanism vs `always`;
- sizing (`evaluation.sizing_ablation.run_sizing`): vol targeting, gross cap and daily overlays vs fixed sizing.
Every variant is a registry trial (so later DSR counts them). The output is JSON-safe (tables and verdicts, no
trade frames) and goes into the evidence package under `row_analysis`; the policy is NOT changed automatically -
an accepted mechanism is a candidate for the next frozen policy, decided by the analyst.
"""
from __future__ import annotations

import math

from sfactory.evaluation.sizing_ablation import run_sizing
from sfactory.policy.edge_state import run_edge_state
from sfactory.policy.ladder import LadderConfig


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_clean(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if hasattr(x, "item") and not isinstance(x, str):        # numpy scalars
        return _clean(x.item())
    return x


def analyze_rows(fm, cache, bars_dev, membership, configs: list, registry=None, edge: bool = True,
                 sizing: bool = True, n_boot: int = 1000) -> dict:
    out = {}
    for cfg in configs:
        if not isinstance(cfg, LadderConfig):
            out[getattr(cfg, "rid", str(cfg))] = {"skipped": "ensemble row: its members are analysed separately"}
            continue
        entry: dict = {}
        if edge:
            e = run_edge_state(fm, cache, bars_dev, membership, cfg, registry=registry, n_boot=n_boot)
            entry["edge_state"] = {"persistence": e["persistence"], "accepted": e["accepted"], "table": e["table"],
                                   "note": e["note"]}
        if sizing:
            s = run_sizing(fm, cache, bars_dev, membership, cfg, registry, n_boot=n_boot)
            entry["sizing"] = {"accepted": s["accepted"], "table": s["table"]}
        out[cfg.rid] = entry
    return _clean(out)


def summarize(analysis: dict) -> dict:
    """One line per row for reports: persistence verdict and accepted mechanisms / sizing variants."""
    return {r: {"persistent": a.get("edge_state", {}).get("persistence", {}).get("verdict"),
                "edge_accepted": a.get("edge_state", {}).get("accepted", []),
                "sizing_accepted": a.get("sizing", {}).get("accepted", [])}
            for r, a in analysis.items() if "skipped" not in a}
