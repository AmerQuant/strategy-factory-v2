import numpy as np
import pytest
from conftest import build

from sfactory.data.regime import market_up_series
from sfactory.engine.cell import run_cell
from sfactory.engine.generic import run_cell_generic
from sfactory.signals.indicators import rsi_wilder
from sfactory.signals.specs import (
    NEUTRAL_MR_EXIT,
    STRUCTURAL_MARKET_UP,
    ExitSpec,
    FilterSpec,
    atr_exec_units,
    exit_signal,
)


def _arr(x):
    return np.array(x, dtype=float)


def test_generic_kernel_reproduces_neutral_exit_engine(rw_market):
    _, cache, _, _ = rw_market
    a = cache.arrays["S001"]
    rsi = rsi_wilder(a.sig_close, 2)
    entry = np.where(np.isnan(rsi), False, rsi < 10)
    ex = exit_signal(NEUTRAL_MR_EXIT, a.sig_close, a.sig_high, 1)
    old = run_cell(entry, a.sig_close, a.sig_high, a.ex_open, a.ex_close, a.div, 1, 5, 1e5, 5.0)
    new = run_cell_generic(entry, ex, a.ex_open, a.ex_close, a.div, np.full(len(entry), np.nan), 1, 5,
                           0.0, 0.0, 1e5, 5.0)
    assert np.allclose(old, new, equal_nan=True)


def test_time_exit_target_and_stop():
    n = 8
    entry = np.zeros(n, dtype=np.bool_)
    entry[0] = True
    none = np.zeros(n, dtype=np.bool_)
    opn = _arr([10, 10, 10, 10, 10, 10, 10, 10])
    close = _arr([10, 10, 10, 10, 10, 10, 10, 10])
    rec = run_cell_generic(entry, none, opn, close, np.zeros(n), np.ones(n), 1, 3, 0.0, 0.0, 1000.0, 0.0)
    assert rec[0, 1] == 1 and rec[0, 2] == 4          # held bars 1,2,3 -> exit at open of 4
    up = _arr([10, 10, 10.5, 11.2, 12, 12, 12, 12])
    rec = run_cell_generic(entry, none, opn, up, np.zeros(n), np.ones(n), 1, 10, 1.0, 0.0, 1000.0, 0.0)
    assert rec[0, 2] == 4                              # close[3] - 10 >= 1*ATR -> exit open 4
    down = _arr([10, 10, 9.5, 7.9, 7, 7, 7, 7])
    rec = run_cell_generic(entry, none, opn, down, np.zeros(n), np.ones(n), 1, 10, 0.0, 2.0, 1000.0, 0.0)
    assert rec[0, 2] == 4                              # 10 - 7.9 >= 2*ATR -> stop at open 4


def test_atr_exec_units_is_causal(rw_market):
    _, cache, _, _ = rw_market
    a = cache.arrays["S002"]
    base = atr_exec_units(a.sig_high, a.sig_low, a.sig_close, a.factor)
    h, lo, c = a.sig_high.copy(), a.sig_low.copy(), a.sig_close.copy()
    h[300:] *= 1.3
    lo[300:] *= 0.7
    c[300:] *= 1.1
    pert = atr_exec_units(h, lo, c, a.factor)
    assert np.array_equal(base[:300], pert[:300], equal_nan=True)


def test_filters_reduce_trades_and_structural_needs_regime():
    _, cache, dev, _ = build("mean_revert", seed=11)
    base = cache.mr_trades("S001", 2, 25.0)
    filt = cache.mr_trades("S001", 2, 25.0, filters=(FilterSpec("above_ma", 200),))
    assert 0 < len(filt) < len(base)
    with pytest.raises(ValueError):
        cache.mr_trades("S001", 2, 25.0, filters=(STRUCTURAL_MARKET_UP,))
    dates, flags = market_up_series(dev)
    cache.set_market_regime(dates, flags, "eqw-ma200")
    m = cache.mr_trades("S001", 2, 25.0, filters=(STRUCTURAL_MARKET_UP,))
    assert len(m) <= len(base)
    assert set(m["signal_date"].to_list()) <= set(dates[flags].tolist())


def test_exit_rsi_above_differs_from_neutral():
    _, cache, _, _ = build("mean_revert", seed=11)
    a = cache.mr_trades("S003", 2, 20.0)
    b = cache.mr_trades("S003", 2, 20.0, exit_spec=ExitSpec("rsi_above", 70, 10))
    assert not a["exit_date"].equals(b["exit_date"])
