import numpy as np
import polars as pl
import pytest
from conftest import build

from sfactory.engine.generic import run_cell_generic
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.signals.methods import DEFAULT, FAMILY, EntrySpec, entry_and_score, reverse_exit

METHODS = sorted(FAMILY)


def _series(seed=0, n=600):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
    h = c * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = c * (1 - np.abs(rng.normal(0, 0.006, n)))
    return h, lo, c


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("direction", [1, -1])
def test_methods_are_causal_and_scale_invariant(method, direction):
    h, lo, c = _series()
    spec = EntrySpec(method, DEFAULT[method], direction)
    e, s = entry_and_score(spec, h, lo, c)
    cut = 400
    h2, lo2, c2 = h.copy(), lo.copy(), c.copy()
    h2[cut:] *= 1.2
    lo2[cut:] *= 0.8
    c2[cut:] *= 1.1
    e2, s2 = entry_and_score(spec, h2, lo2, c2)
    assert np.array_equal(e[:cut], e2[:cut]) and np.allclose(s[:cut], s2[:cut], equal_nan=True)
    e3, _ = entry_and_score(spec, h * 3.7, lo * 3.7, c * 3.7)
    assert np.array_equal(e, e3)
    assert e.any()
    if FAMILY[method] == "TF":
        r, r2 = reverse_exit(spec, h, lo, c), reverse_exit(spec, h2, lo2, c2)
        assert np.array_equal(r[:cut], r2[:cut])


def test_trailing_stop_exits_after_giveback():
    n = 8
    entry = np.zeros(n, dtype=np.bool_)
    entry[0] = True
    none = np.zeros(n, dtype=np.bool_)
    opn = np.full(n, 10.0)
    close = np.array([10, 10, 12, 14, 13.5, 11.9, 11, 11], dtype=float)
    rec = run_cell_generic(entry, none, opn, close, np.zeros(n), np.ones(n), 1, 100, 0.0, 0.0, 1000.0, 0.0, 2.0)
    assert rec[0, 2] == 6  # peak move 4 at bar 3; close 11.9 -> move 1.9 <= 4 - 2 -> exit at open of bar 6


def test_catalog_has_18_unique_rows():
    rows = equity_rows()
    assert len(rows) == 18 and len({r.rid for r in rows}) == 18


def test_rows_find_edges_only_where_they_exist():
    fm, cache, dev, mem = build("random_walk", seed=11)
    for cfg in equity_rows(LadderConfig(rung="A1")):
        assert run_ladder(fm, cache, dev, mem, cfg).stats["t_stat"] < 2.0, cfg.rid
    fm, cache, dev, mem = build("trending", seed=11)
    tf = run_ladder(fm, cache, dev, mem, LadderConfig(rung="A1", method="donchian_break"))
    mr = run_ladder(fm, cache, dev, mem, LadderConfig(rung="A1", method="rsi"))
    assert tf.stats["t_stat"] > 2.0 and mr.stats["t_stat"] < 0
    fm, cache, dev, mem = build("mean_revert", seed=11)
    assert run_ladder(fm, cache, dev, mem, LadderConfig(rung="A1", method="ibs")).stats["t_stat"] > 3.0
    assert run_ladder(fm, cache, dev, mem, LadderConfig(rung="A1", method="ma_cross")).stats["t_stat"] < 0


def test_tf_ladder_future_perturbation():
    fm, cache, dev, mem = build("trending", seed=3)
    cfg = LadderConfig(rung="A4", method="supertrend", direction=-1)
    base = run_ladder(fm, cache, dev, mem, cfg)
    k = 5
    dp_k = fm.dev_folds()[k].dp
    rng = np.random.default_rng(2)

    def perturb(bars):
        noise = pl.Series(rng.uniform(0.8, 1.2, len(bars)))
        m = pl.when(pl.col("date") >= dp_k).then(noise).otherwise(1.0)
        return bars.with_columns([(pl.col(c) * m) for c in ["open", "high", "low", "close", "volume"]])

    fm2, cache2, dev2, mem2 = build("trending", seed=3, bars_transform=perturb)
    assert run_ladder(fm2, cache2, dev2, mem2, cfg).decisions[: k + 1] == base.decisions[: k + 1]
