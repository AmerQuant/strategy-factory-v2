import json
from dataclasses import replace

import pytest
from conftest import CFG

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.evaluation.evidence import build_evidence, dumps
from sfactory.evaluation.holdout import dev_criteria, run_holdout
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.registry.repo import HoldoutError, Registry
from sfactory.timeline.folds import FoldManager


def _pipeline(kind, seed=11):
    bars, divs, mem = make_market(20, 2600, seed=seed, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    dev_cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), f"syn-{kind}-{seed}")
    rows = [r for r in equity_rows(LadderConfig(max_positions=5, max_new_per_day=2)) if r.method in ("rsi", "ibs", "ma_cross")]
    reg = Registry()
    cat = run_catalog(fm, dev_cache, dev, mem, rows, registry=reg)
    full_cache = TradeCache(prepare_arrays(bars, divs), f"syn-{kind}-{seed}-full")
    return fm, full_cache, bars, mem, cat, reg


def test_holdout_is_preregistered_one_shot_and_evidence_is_json():
    fm, full_cache, bars, mem, cat, reg = _pipeline("mean_revert")
    assert cat["accepted"]
    res = run_holdout(fm, full_cache, bars, mem, cat, reg, "dv1")
    assert res["status"] in ("pass", "fail") and set(res["checks"]) == {"sharpe", "max_dd", "benchmark"}
    assert res["status"] == "pass"
    with pytest.raises(HoldoutError):                  # burned: a second opening is refused
        run_holdout(fm, full_cache, bars, mem, cat, reg, "dv1")
    with pytest.raises(HoldoutError):
        reg.register_holdout("dv1", "anything", {})
    pkg = build_evidence(cat, res, {"catalog": "test"})
    txt = dumps(pkg)
    back = json.loads(txt)
    assert back["trials_in_registry"] == cat["n_trials"] and back["holdout"]["policy_hash"] == res["policy_hash"]


def test_open_requires_registration_and_same_policy():
    reg = Registry()
    with pytest.raises(HoldoutError):
        reg.open_holdout("dv2", "h")
    reg.register_holdout("dv2", "h", {"x": 1})
    with pytest.raises(HoldoutError):
        reg.open_holdout("dv2", "other")
    assert reg.open_holdout("dv2", "h") == {"x": 1}


def test_random_walk_has_nothing_to_test():
    fm, full_cache, bars, mem, cat, reg = _pipeline("random_walk")
    assert run_holdout(fm, full_cache, bars, mem, cat, reg, "dv3")["status"] == "nothing_to_test"


def test_criteria_come_from_dev_only():
    import numpy as np
    x = np.random.default_rng(0).normal(50, 500, 1200)
    a = dev_criteria(x, 500)
    b = dev_criteria(x.copy(), 500)
    assert a == b and a["sharpe_min"] < a["dev_sharpe"]
    assert replace(LadderConfig(), rung="A1").rung == "A1"
