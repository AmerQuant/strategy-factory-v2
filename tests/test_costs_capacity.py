from datetime import date

import numpy as np
import polars as pl
from conftest import build

from sfactory.costs.model import CostModel, SymbolCost
from sfactory.engine.cache import TradeCache
from sfactory.evaluation.benchmarks import evaluate_row, random_ranking
from sfactory.policy.grid import build_grid
from sfactory.policy.rsi_row import RowConfig, run_row
from sfactory.portfolio.capacity import max_concurrent, simulate_capacity


def test_cost_model_per_symbol_and_stress():
    cm = CostModel(SymbolCost(4, 1, 1), {"X": SymbolCost(20, 1, 1)})
    assert cm.per_side_bps("A") == 4.0 and cm.per_side_bps("X") == 12.0
    assert cm.stressed(1.5).per_side_bps("A") == 6.0
    assert cm.fingerprint() != cm.stressed(1.5).fingerprint()


def test_cache_uses_symbol_cost_and_separate_keys():
    _, cache, _, _ = build("random_walk", seed=4)
    cheap = TradeCache(cache.arrays, "v", cost_model=CostModel.flat(1.0))
    pricey = TradeCache(cache.arrays, "v", cost_model=CostModel(SymbolCost(0, 1, 0), {"S001": SymbolCost(0, 50, 0)}))
    a, b = cheap.rsi_mr("S001", 2, 10.0), pricey.rsi_mr("S001", 2, 10.0)
    assert np.allclose(b["cost"].to_numpy(), 50 * a["cost"].to_numpy())
    assert "score" in a.columns and (a["score"] < 10).all()


def _cands(rows):
    d = lambda x: date(2020, 1, x)
    return pl.DataFrame([{"symbol": s, "signal_date": d(e - 1), "entry_date": d(e), "exit_date": d(x),
                          "net_pnl": 100.0, "gross_pnl": 100.0, "cost": 0.0, "dividends": 0.0, "shares": 1.0,
                          "score": sc} for s, e, x, sc in rows])


def test_capacity_ranks_limits_and_blocks():
    c = _cands([("A", 2, 6, 5.0), ("B", 2, 6, 1.0), ("C", 2, 6, 3.0),   # day 2: 3 signals, max 2 new
                ("D", 3, 5, 0.5),                                       # day 3: book full (2 open)
                ("E", 6, 8, 2.0), ("B", 6, 9, 1.0)])                    # day 6: slots free again
    out = simulate_capacity(c, max_positions=2, max_new_per_day=2, capital=200.0, cache_notional=100.0)
    got = {(r["symbol"], r["entry_date"].day) for r in out.iter_rows(named=True)}
    assert got == {("B", 2), ("C", 2), ("B", 6), ("E", 6)}
    assert np.allclose(out["net_pnl"].to_numpy(), 100.0)  # (200/2)/100 = 1x
    assert max_concurrent(out) <= 2


def test_random_ranker_is_seeded():
    c = _cands([(s, 2, 4, 1.0) for s in "ABCDEFGH"])
    a = simulate_capacity(c, 3, 3, 3.0, 1.0, ranker="random", seed=1)["symbol"].to_list()
    b = simulate_capacity(c, 3, 3, 3.0, 1.0, ranker="random", seed=1)["symbol"].to_list()
    assert a == b and len(a) == 3


def test_row_in_capacity_mode_respects_book_limits():
    cfg = RowConfig(select=True, max_positions=5, max_new_per_day=2)
    fm, cache, dev, mem = build("mean_revert", seed=11)
    res = run_row(fm, cache, dev, mem, cfg)
    assert max_concurrent(res.oos_trades) <= 5
    assert res.oos_trades.group_by("entry_date").len()["len"].max() <= 2
    assert res.stats["max_dd"] < 1.0  # sized to capital, unlike cell mode
    grid = build_grid(fm, cache, dev, mem, cfg)
    rr = random_ranking(grid, cfg, n_draws=5)
    assert len(rr) == 5
    rep = evaluate_row(grid, cfg, n_draws=20)
    assert {"random_ranking", "stats"} <= rep.keys()
