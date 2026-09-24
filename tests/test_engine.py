import numpy as np

from sfactory.engine.cell import run_cell


def _arr(x):
    return np.array(x, dtype=float)


def test_entry_next_open_exit_on_prev_high_break_with_costs_and_dividend():
    entry = np.array([False, True, False, False, False, False])
    close = _arr([10, 9, 9.5, 10.5, 10, 10])
    high = _arr([10.2, 9.8, 9.9, 10.6, 10.1, 10.1])
    opn = _arr([10, 9.5, 9.0, 9.6, 10.4, 10])
    div = _arr([0, 0, 0, 0.1, 0, 0])
    rec = run_cell(entry, close, high, opn, close, div, 1, 5, 900.0, 10.0)
    assert rec.shape[0] == 1
    sig, ent, ex, ent_px, ex_px, shares, gross, cost, divs, net, forced = rec[0]
    assert (sig, ent, ex) == (1, 2, 4)          # close[3]=10.5 > high[2]=9.9 -> exit at open[4]
    assert ent_px == 9.0 and ex_px == 10.4 and shares == 100.0
    assert np.isclose(gross, 140.0)
    assert np.isclose(cost, 10e-4 * 100 * (9.0 + 10.4))
    assert np.isclose(divs, 10.0)               # held over ex-date 3
    assert np.isclose(net, gross - cost + divs) and forced == 0


def test_max_hold_and_forced_exit_at_data_end():
    entry = np.array([True, False, False, False])
    close = _arr([10, 9, 8, 7])
    high = _arr([10.5, 9.5, 8.5, 7.5])
    opn = _arr([10, 9.8, 8.8, 7.8])
    rec = run_cell(entry, close, high, opn, close, np.zeros(4), 1, 10, 1000.0, 0.0)
    assert rec[0, 2] == 3 and rec[0, 4] == 7.0 and rec[0, 10] == 1


def test_short_direction_pnl_sign():
    entry = np.array([True, False, False, False])
    close = _arr([10, 11, 9, 9])
    high = _arr([10, 11, 9, 9])  # for shorts the 'high' input is the level whose break exits
    opn = _arr([10, 10, 10, 8])
    rec = run_cell(entry, close, high, opn, close, np.zeros(4), -1, 5, 1000.0, 0.0)
    assert rec[0, 6] == -100.0 * (8 - 10)
