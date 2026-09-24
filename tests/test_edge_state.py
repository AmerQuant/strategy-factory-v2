from dataclasses import replace
from datetime import date
from itertools import pairwise

import numpy as np
import polars as pl
import pytest
from conftest import CFG

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import business_days
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.policy.edge_state import (
    MODES,
    ActivationConfig,
    decide_sub,
    run_activation,
    run_edge_state,
    state_persistence,
)
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.registry.repo import Registry
from sfactory.signals.methods import EntrySpec
from sfactory.signals.trendiness import feature_series
from sfactory.timeline.folds import FoldManager

ROW = LadderConfig(rung="A0", method="rsi", direction=1, fixed_threshold=20.0, min_price=0.0, min_dollar_vol=0.0)


def switching_market(n_symbols=24, n_days=2600, seed=3, mean_regime=700, strength=0.45, off_strength=-0.2,
                     always_on=False, flags=False):
    """MR edge that switches on and off per symbol in persistent regimes (off = mild momentum, where
    buying the dip loses)."""
    rng = np.random.default_rng(seed)
    days = business_days(date(2010, 1, 4), n_days)
    rows = []
    for i in range(n_symbols):
        vol = rng.uniform(0.012, 0.02)
        on = np.zeros(n_days, bool)
        state, t = bool(rng.integers(0, 2)), 0
        while t < n_days:
            L = int(rng.exponential(mean_regime)) + 60
            on[t:t + L] = state or always_on
            state, t = not state, t + L
        eps = rng.normal(0, vol, n_days)
        r = eps.copy()
        for k in range(1, n_days):
            r[k] = eps[k] - (strength if on[k] else off_strength) * r[k - 1]
        close = 50 * np.exp(np.cumsum(r))
        open_ = np.r_[close[0], close[:-1] * np.exp(rng.normal(0, vol * 0.2, n_days - 1))]
        hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.4, n_days)))
        lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.4, n_days)))
        rows.append(pl.DataFrame({"symbol": f"S{i:03d}", "date": days, "open": open_, "high": hi, "low": lo,
                                  "close": close, "volume": np.full(n_days, 1e6), "on": on}))
    bars = pl.concat(rows)
    if not flags:
        bars = bars.drop("on")
    mem = pl.DataFrame({"symbol": [f"S{i:03d}" for i in range(n_symbols)], "start": [days[0]] * n_symbols,
                        "end": [None] * n_symbols}, schema={"symbol": pl.Utf8, "start": pl.Date, "end": pl.Date})
    return bars, mem


def _build(bars, mem, tag):
    divs = pl.DataFrame(schema={"symbol": pl.Utf8, "ex_date": pl.Date, "amount": pl.Float64})
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    return fm, TradeCache(prepare_arrays(dev, divs), tag, cost_bps=2.0), dev, mem


@pytest.fixture(scope="module")
def switching():
    return _build(*switching_market(), "switch-3")


def test_sub_folds_tile_the_fold_and_lookback_is_causal():
    fm = FoldManager(CFG)
    f = fm.dev_folds()[0]
    subs = fm.sub_folds(f, 1)
    assert len(subs) == 6 and subs[0].dp == f.oos_start and subs[-1].oos_end == f.oos_end
    assert all(a.oos_end == b.dp for a, b in pairwise(subs)) and all(s.index == f.index for s in subs)
    t = pl.DataFrame({"entry_date": [date(2012, 1, 3), date(2012, 6, 1), date(2012, 12, 20)],
                      "exit_date": [date(2012, 1, 9), date(2012, 6, 8), date(2013, 1, 4)], "net_pnl": [1.0, 2.0, 3.0]})
    lb = fm.slice_lookback(t, date(2013, 1, 1), 12)
    assert lb["net_pnl"].to_list() == [1.0, 2.0]          # the open-at-DP trade is excluded
    assert fm.slice_lookback(t, date(2013, 1, 1), 7)["net_pnl"].to_list() == [2.0]


def test_features_are_causal_and_sane():
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    for k in ("efficiency", "variance_ratio", "autocorr"):
        a = feature_series(k, c, 60)
        b = feature_series(k, np.r_[c[:300], c[300:] * 1.7], 60)
        assert np.allclose(a[:300], b[:300], equal_nan=True)
    line = np.linspace(10, 20, 100)
    assert np.allclose(feature_series("efficiency", line, 20)[20:], 1.0)


def test_always_equals_run_ladder(switching):
    fm, cache, dev, mem = switching
    a = run_activation(fm, cache, dev, mem, ROW, ActivationConfig("always"))
    b = run_ladder(fm, cache, dev, mem, ROW)
    key = ["symbol", "signal_date"]
    assert a.oos_trades.sort(key)["net_pnl"].to_list() == b.oos_trades.sort(key)["net_pnl"].to_list()
    assert a.stats["exposure"] == 1.0 and a.stats["switches"] == 0


@pytest.mark.parametrize("mode", [m for m in MODES if m != "always"])
def test_future_perturbation_leaves_past_decisions_unchanged(mode):
    bars, mem = switching_market(n_symbols=10, seed=5)
    cut = date(2015, 3, 1)
    rng = np.random.default_rng(1)
    noise = pl.Series(np.exp(rng.normal(0, 0.02, len(bars))))
    pert = bars.with_columns([pl.when(pl.col("date") >= cut).then(pl.col(c) * noise).otherwise(pl.col(c)).alias(c)
                              for c in ("open", "high", "low", "close")])
    pert = pert.with_columns(pl.max_horizontal("open", "high", "close").alias("high"),
                             pl.min_horizontal("open", "low", "close").alias("low"))
    act = ActivationConfig(mode)
    r1 = run_activation(*_build(bars, mem, "a"), ROW, act)
    r2 = run_activation(*_build(pert, mem, "b"), ROW, act)

    def past(res):
        out = []
        for d in res.decisions:
            if date.fromisoformat(d["dp"]) <= cut:
                out.append((d["threshold"], d["trend_calibration"]))
            out += [s for s in d["sub_dps"] if date.fromisoformat(s["dp"]) <= cut]
        return out

    assert past(r1) == past(r2) and len(past(r1)) > 10


def test_soft_weights_have_mean_one_and_bounds(switching):
    fm, cache, dev, mem = switching
    act = ActivationConfig("soft_weight", w_max=2.0)
    r = run_activation(fm, cache, dev, mem, ROW, act)
    for d in r.decisions:
        for s in d["sub_dps"]:
            assert s["n_active"] <= s["n_eligible"]
            assert all(0.0 <= w <= 2.0 for w in s["weights"].values())
    fold = fm.dev_folds()[3]                           # direct check on one sub-DP
    per = {s: cache.trades(s, EntrySpec("rsi", ROW.fixed), ROW.neutral) for s in sorted(cache.arrays)}
    w = decide_sub(fm, cache, fm.sub_folds(fold)[0], per, act, {}, None)
    v = np.array(list(w.values()))
    assert abs(v.mean() - 1) < 0.05 and v.min() >= 0 and v.max() <= 2.0 and v.std() > 0


def test_switching_edge_is_persistent_and_two_clock_helps(switching):
    fm, cache, dev, mem = switching
    reg = Registry()
    rep = run_edge_state(fm, cache, dev, mem, ROW, modes=("always", "two_clock", "shadow", "soft_weight"),
                         registry=reg, n_boot=400)
    assert rep["persistence"]["verdict"] == "persistent"
    t = {r["mode"]: r for r in rep["table"]}
    assert t["two_clock"]["exposure"] < 0.9 and t["two_clock"]["sharpe"] > t["always"]["sharpe"]
    assert "two_clock" in rep["accepted"]
    assert reg.count_trials() == 4                        # every mechanism is a registry trial


def test_random_walk_accepts_nothing(rw_market):
    fm, cache, dev, mem = rw_market
    row = replace(ROW, min_dollar_vol=1e6)
    rep = run_edge_state(fm, cache, dev, mem, row, n_boot=300)
    assert rep["persistence"]["verdict"] == "not_persistent" and rep["accepted"] == []


def test_always_on_edge_is_not_persistent_in_state():
    """Every symbol has the same permanent edge: past rank does not predict future rank -> no mechanism."""
    fm, cache, dev, mem = _build(*switching_market(n_symbols=16, seed=9, always_on=True), "on")
    p = state_persistence(fm, cache, dev, mem, ROW)
    assert p["verdict"] == "not_persistent"


def test_trendiness_calibration_is_in_fold(switching):
    fm, cache, dev, mem = switching
    r = run_activation(fm, cache, dev, mem, ROW, ActivationConfig("trendiness", feature="autocorr"))
    cals = [d["trend_calibration"] for d in r.decisions]
    assert all(c["side"] in ("high", "low", "none") for c in cals)
    # autocorr < median should be the MR-favourable side when a side is picked
    picked = [c["side"] for c in cals if c["side"] != "none"]
    assert picked and picked.count("low") >= picked.count("high")
