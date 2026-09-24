"""Evidence package (design 14.3): one JSON document per policy, the single input of the Persian report.

The report itself is written by an LLM in chat from this JSON + docs/templates/report_prompt_fa.md, so every
number in the report is traceable to the registry and to this package.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x if isinstance(x, (int, str)) or x is None else str(x)


def _weekly_curve(dates, daily_a, daily_b, step: int = 5) -> dict:
    ca, cb = np.cumsum(daily_a), np.cumsum(daily_b)
    idx = list(range(0, len(dates), step)) + ([len(dates) - 1] if len(dates) % step != 1 else [])
    return {"dates": [str(dates[i])[:10] for i in idx], "combined": [float(ca[i]) for i in idx],
            "benchmark": [float(cb[i]) for i in idx]}


def build_evidence(catalog_report: dict, holdout_report: dict | None, meta: dict) -> dict:
    accepted = catalog_report["accepted"]
    rows = []
    for t in catalog_report["table"]:
        r = catalog_report["results"][t["i"]]
        rows.append({**{k: v for k, v in t.items() if k != "i"},
                     "fold_decisions": r.decisions if t["i"] in accepted else None})
    pkg = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "meta": meta,
        "trials_in_registry": catalog_report["n_trials"],
        "rows": rows,
        "combined_dev": catalog_report["combined"],
        "benchmark_all_rows_equal_dev": catalog_report["benchmark_all_equal"],
        "effective_n_all_rows": catalog_report["effective_n_all"],
        "dev_curve": _weekly_curve(catalog_report["dates"], catalog_report["combined_daily"],
                                   catalog_report["benchmark_daily"]) if "combined_daily" in catalog_report else None,
        "robustness_of_accepted_rows": catalog_report.get("robustness"),
        "spa_any_vs_cash": catalog_report.get("spa_any_vs_cash"),
        "spa_combined_vs_all_equal": catalog_report.get("spa_combined_vs_all_equal"),
        "family_ensembles": catalog_report.get("ensembles"),
        "meta_grid": meta.get("meta_grid"),
        "holdout": holdout_report,
    }
    return _clean(pkg)


def dumps(pkg: dict) -> str:
    return json.dumps(pkg, ensure_ascii=False, indent=1, sort_keys=True)
