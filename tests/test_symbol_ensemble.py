from dataclasses import replace

import numpy as np
import polars as pl
from conftest import CFG

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.data.universe import static_membership
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.forward.live import decide_book
from sfactory.policy.catalog import ensemble_rows, equity_rows, fx_rows
from sfactory.policy.ensemble import run_ensemble
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldManager

KINDS = ["mean_revert", "random_walk"] * 6          # 12 FX-like symbols: S000, S002, ... mean-revert


def _fx(transform=None):
    bars, divs, _ = make_market(12, 2600, seed=21, kinds=KINDS, dividends=False, listings=False)
    if transform is not None:
        bars = transform(bars)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    return fm, TradeCache(prepare_arrays(dev, divs), "syn-fx"), dev, static_membership(dev)


def test_symbol_selection_picks_the_symbols_with_edge_and_beats_random_n():
    fm, cache, dev, mem = _fx()
    cfg = next(r for r in fx_rows(LadderConfig(rung="A1"), top_n=4) if r.method == "rsi" and r.direction == 1)
    assert cfg.rid == "MR-RSI-BUY-FX-TOP4"
    res = run_ladder(fm, cache, dev, mem, cfg)
    picks = [s for d in res.decisions for s in d["symbols"]]
    mr = {f"S{i:03d}" for i in range(0, 12, 2)}
    assert picks and sum(s in mr for s in picks) / len(picks) >= 0.75
    rand = [run_ladder(fm, cache, dev, mem, replace(cfg, symbol_ranker="random", symbol_seed=k)).stats["sharpe"]
            for k in range(12)]
    assert res.stats["sharpe"] > np.percentile(rand, 90)
    pooled = run_ladder(fm, cache, dev, mem, replace(cfg, symbol_select=0))
    assert res.stats["expectancy"] > pooled.stats["expectancy"]


def test_symbol_selection_is_causal():
    cfg = replace(fx_rows(LadderConfig(rung="A3"), top_n=3)[0])
    fm, cache, dev, mem = _fx()
    base = run_ladder(fm, cache, dev, mem, cfg)
    dp_k = fm.dev_folds()[5].dp
    rng = np.random.default_rng(3)

    def perturb(bars):
        m = pl.when(pl.col("date") >= dp_k).then(pl.Series(rng.uniform(0.8, 1.2, len(bars)))).otherwise(1.0)
        return bars.with_columns([(pl.col(c) * m) for c in ["open", "high", "low", "close", "volume"]])

    fm2, cache2, dev2, mem2 = _fx(perturb)
    assert run_ladder(fm2, cache2, dev2, mem2, cfg).decisions[:6] == base.decisions[:6]


def test_ensemble_row_is_one_trial_splits_capital_and_expands_live():
    bars, divs, mem = make_market(20, 2600, seed=11, kind="mean_revert")
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev, ddev = fm.dev_view(bars), fm.dev_view(divs, "ex_date")
    cache = TradeCache(prepare_arrays(dev, ddev), "syn-mr")
    rows = [r for r in equity_rows(LadderConfig(rung="A1", max_positions=6, max_new_per_day=2))
            if r.method in ("rsi", "ibs", "consec")]
    ens = next(e for e in ensemble_rows(rows) if e.rid.startswith("ENS-MR-BUY"))
    assert len(ens.members) == 3 and all(m.capital == ens.capital / 3 for m in ens.expanded())
    reg = Registry()
    er = run_ensemble(fm, cache, dev, mem, ens, reg)
    assert reg.count_trials() == 1 and set(er.oos_trades["member"].unique()) == {m.rid for m in ens.members}
    assert er.stats["sharpe"] > 0
    rep = run_catalog(fm, cache, dev, mem, rows + ensemble_rows(rows), divs_dev=ddev, spa_boot=100)
    assert any(t["ensemble"] for t in rep["table"]) and rep["accepted"]
    book = decide_book(fm, cache, dev, mem, [ens], fm.dev_folds()[4].dp)
    assert len(book) == 3 and all(k.startswith(ens.rid + "/") for k in book)
