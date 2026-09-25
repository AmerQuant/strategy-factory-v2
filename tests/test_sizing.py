from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest
from conftest import build

from sfactory.data.synthetic import business_days
from sfactory.evaluation.sizing_ablation import run_sizing
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.portfolio.sizing import (
    drawdown_brake,
    exposure_cap,
    max_gross_exposure,
    signal_vol,
    trailing_vol,
    vol_target_daily,
    vol_weights,
)
from sfactory.registry.repo import Registry

ROW = LadderConfig(rung="A1", method="rsi")


def hetero_mr_market(n_symbols=8, n_days=2600, seed=4, k=0.25, lo=0.004, hi=0.04, regime=120):
    """Mean reversion whose volatility switches between calm and wild regimes (per symbol, random lengths)."""
    rng = np.random.default_rng(seed)
    days = business_days(date(2010, 1, 4), n_days)
    out = []
    for i in range(n_symbols):
        sig = np.empty(n_days)
        t = 0
        while t < n_days:
            n = int(rng.integers(regime // 2, regime * 2))
            sig[t:t + n] = rng.choice([lo, hi])
            t += n
        z = rng.normal(0, 1, n_days)
        r = np.empty(n_days)
        r[0] = z[0] * sig[0]
        for t in range(1, n_days):
            r[t] = sig[t] * (z[t] - k * r[t - 1] / sig[t - 1])
        close = 50 * np.exp(np.cumsum(r))
        opn = np.r_[close[0], close[:-1]]
        out.append(pl.DataFrame({"symbol": f"S{i:03d}", "date": days, "open": opn,
                                 "high": np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.3, n_days)) * sig),
                                 "low": np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.3, n_days)) * sig),
                                 "close": close, "volume": 1e6}))
    divs = pl.DataFrame(schema={"symbol": pl.Utf8, "ex_date": pl.Date, "amount": pl.Float64})
    mem = pl.DataFrame({"symbol": [f"S{i:03d}" for i in range(n_symbols)], "start": days[0], "end": None},
                       schema={"symbol": pl.Utf8, "start": pl.Date, "end": pl.Date})
    return pl.concat(out), divs, mem


def test_trailing_vol_matches_numpy():
    c = np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, 300)))
    r = np.diff(np.log(c))
    v = trailing_vol(c, 20)
    assert np.isnan(v[:20]).all()
    for i in (20, 57, 299):
        assert np.isclose(v[i], np.std(r[i - 20:i], ddof=1))


def test_vol_weights_are_causal_and_bounded(rw_market):
    _, cache, _, _ = rw_market
    t = cache.trades("S001", *_spec())
    _, w = vol_weights(cache, t, w_max=3.0)
    assert (w > 0).all() and (w <= 3.0).all() and w.std() > 0
    a = cache.arrays["S001"]
    cut = len(a.dates) // 2
    before = t["signal_date"].to_numpy() < a.dates[cut]
    sc = a.sig_close.copy()
    a.sig_close = np.r_[sc[:cut], sc[cut:] * np.exp(np.random.default_rng(1).normal(0, 0.05, len(sc) - cut))]
    cache.__dict__.pop("_sizing_vol", None)
    try:
        v2 = signal_vol(cache, t)
    finally:
        a.sig_close = sc
        cache.__dict__.pop("_sizing_vol", None)
    assert np.allclose(signal_vol(cache, t)[before], v2[before], equal_nan=True)


def _spec():
    from sfactory.signals.methods import EntrySpec
    from sfactory.signals.specs import NEUTRAL_MR_EXIT
    return EntrySpec("rsi", 20.0), NEUTRAL_MR_EXIT


def test_fast_leg_catches_volatility_shocks():
    c = np.r_[np.full(40, 100.0) * np.exp(np.cumsum(np.full(40, 1e-4))), 100 * np.exp(-0.08)]
    slow, fast = trailing_vol(c, 20)[-1], trailing_vol(c, 5)[-1]
    assert fast > 1.5 * slow                     # the shock bar dominates the short window


def test_exposure_cap_is_never_exceeded(rw_market):
    fm, cache, dev, mem = rw_market
    t = run_ladder(fm, cache, dev, mem, ROW).oos_trades
    assert max_gross_exposure(t) > 2e5
    capped = exposure_cap(t, 100_000, 1.5)
    assert max_gross_exposure(capped) <= 1.5e5 * (1 + 1e-9) and 0 < len(capped) < len(t)
    assert len(exposure_cap(t, 100_000, 1e6)) == len(t)


def test_portfolio_vol_target_is_causal_and_equalises_risk():
    rng = np.random.default_rng(3)
    sig = np.repeat(rng.choice([0.3, 3.0], 24), 250)           # volatility regimes longer than the lookback
    x = sig * (0.08 + rng.normal(0, 1, len(sig)))
    y, lev = vol_target_daily(x, 0.5)                           # leverage stays inside lev_max
    _, lev2 = vol_target_daily(np.r_[x[:1000], x[1000:] * 7], 0.5)
    assert np.array_equal(lev[:1001], lev2[:1001])              # day t uses days before t only
    settled = np.r_[False, sig[1:] == sig[:-1]] & (np.arange(len(sig)) % 250 >= 80)
    hi, lo = settled & (sig == 3.0), settled & (sig == 0.3)
    assert x[hi].std() / x[lo].std() > 8                        # raw pnl risk differs 10x between regimes
    assert 0.7 < y[hi].std() / y[lo].std() < 1.4                # the overlay equalises it


def test_drawdown_brake_cuts_and_resumes():
    x = np.r_[np.full(20, 100.0), np.full(20, -1500.0), np.full(200, 200.0)]
    out, e = drawdown_brake(x, 100_000, dd_limit=0.15, cut=0.5)
    first = int(np.argmax(e < 1))
    assert 20 < first < 40 and (e[first:40] == 0.5).all() and e[-1] == 1.0
    assert np.isclose(out[first], x[first] * 0.5)
    assert (drawdown_brake(np.full(50, 10.0), 100_000)[1] == 1).all()


def test_sizing_is_a_noop_by_default_and_named(rw_market):
    fm, cache, dev, mem = rw_market
    a = run_ladder(fm, cache, dev, mem, ROW)
    assert a.config.rid == "MR-RSI-BUY-EQ"
    assert replace(ROW, sizing="vol", max_gross=1.5).rid == "MR-RSI-BUY-EQ-VOL-CAP1.5"
    with pytest.raises(ValueError):
        run_ladder(fm, cache, dev, mem, replace(ROW, sizing="kelly"))


def test_sizing_ablation_gate_and_registry():
    fm, cache, dev, mem = build("random_walk", seed=11)
    reg = Registry()
    r = run_sizing(fm, cache, dev, mem, replace(ROW, max_positions=10), reg)
    assert [t["variant"] for t in r["table"]] == ["fixed", "vol", "cap", "vol+cap", "fixed+vol_target",
                                                   "fixed+dd_brake"]
    assert r["accepted"] == []                   # nothing to improve on a random walk
    assert reg.count_trials() == 6


def test_vol_sizing_on_volatility_regimes_is_judged_by_the_gate():
    """Documented finding: on regime-switching MR, entries cluster at volatility shocks; inverse-vol sizing lowers
    drawdown in most seeds but does not beat fixed sizing on Sharpe, so the gate rejects it."""
    from conftest import CFG

    from sfactory.data.adjust import add_adj_factor
    from sfactory.engine.cache import TradeCache, prepare_arrays
    from sfactory.timeline.folds import FoldManager
    bars, divs, mem = hetero_mr_market(seed=5)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, divs), "hetero", cost_bps=2.0)
    r = run_sizing(fm, cache, dev, mem, replace(ROW, min_price=0.0, min_dollar_vol=0.0, min_is_trades=10))
    t = {x["variant"]: x for x in r["table"]}
    assert t["fixed"]["sharpe"] > 1.0
    assert t["vol"]["max_dd"] < t["fixed"]["max_dd"] and not t["vol"]["accepted"]
