from conftest import CFG

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.evaluation.evidence import build_evidence, dumps
from sfactory.evaluation.meta_grid import run_meta_grid
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldManager


def _market(kind):
    bars, divs, mem = make_market(20, 2600, seed=11, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev, ddev = fm.dev_view(bars), fm.dev_view(divs, "ex_date")
    return fm, TradeCache(prepare_arrays(dev, ddev), f"syn-{kind}"), dev, ddev, mem


ROWS = [r for r in equity_rows(LadderConfig(max_positions=5, max_new_per_day=2))
        if r.method in ("rsi", "ibs", "ma_cross")]


def test_catalog_with_robustness_spa_and_ensembles():
    fm, cache, dev, ddev, mem = _market("mean_revert")
    rep = run_catalog(fm, cache, dev, mem, ROWS, divs_dev=ddev, spa_boot=200)
    assert rep["accepted"] and set(rep["robustness"]) >= {rep["table"][i]["row"] for i in rep["accepted"]}
    assert all(rep["table"][i]["robust"] for i in rep["accepted"])
    assert rep["spa_any_vs_cash"]["p_value"] < 0.05
    assert set(rep["ensembles"]) == {"MR-BUY", "MR-SELL", "TF-BUY", "TF-SELL"}
    pkg = build_evidence(rep, None, {})
    assert "family_ensembles" in dumps(pkg)
    fm, cache, dev, ddev, mem = _market("random_walk")
    rw = run_catalog(fm, cache, dev, mem, ROWS, divs_dev=ddev, spa_boot=200)
    assert rw["accepted"] == [] and rw["spa_any_vs_cash"]["p_value"] > 0.05


def test_meta_grid_counts_every_setting_as_trials():
    _, cache, dev, _, mem = _market("mean_revert")
    reg = Registry()
    mg = run_meta_grid(CFG, cache, dev, mem, ROWS, is_years=(2, 3), capacity=((5, 2),), registry=reg)
    assert len(mg["settings"]) == 2 and mg["trials_in_registry"] == 2 * len(ROWS)
    assert mg["share_beating_benchmark"] == 1.0 and mg["pbo_across_settings"] is not None
