from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest

from sfactory.broker.orders import Order, RowBook, allocate, net_orders
from sfactory.broker.reconcile import reconcile
from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel, SymbolCost
from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.forward.live import BookEntry
from sfactory.forward.paper import arrays_upto, plan_day, run_paper
from sfactory.policy.ladder import LadderConfig
from sfactory.signals.methods import EntrySpec
from sfactory.signals.specs import ExitSpec

COST = CostModel.flat(2.0)


@pytest.fixture(scope="module")
def mkt():
    bars, divs, _ = make_market(6, 1200, seed=8, kind="mean_revert", dividends=False, listings=False)
    bars = add_adj_factor(bars, divs)
    arrays = prepare_arrays(bars, divs)
    return arrays, sorted(arrays)


def _book(cfg, thr, ex, syms):
    return {cfg.rid: BookEntry(cfg.rid, cfg, thr, ex.id, (), tuple(syms), {})}


def _days(arrays, start, end):
    d = arrays["S000"].dates
    return [x.astype(object) for x in d[(d >= np.datetime64(start)) & (d < np.datetime64(end))]]


@pytest.mark.parametrize("method,thr,ex", [
    ("rsi", 15.0, ExitSpec("prev_high", 0, 5)),
    ("rsi", 15.0, ExitSpec("prev_high", 0, 10, stop_atr=3.0)),
    ("donchian_break", 20.0, ExitSpec("none", 0, 250, trail_atr=3.0)),
])
def test_paper_replay_reproduces_research_trades(mkt, method, thr, ex):
    arrays, syms = mkt
    cfg = LadderConfig(method=method, exits=(ex,))
    start, end = date(2012, 6, 1), date(2013, 12, 31)
    pt = run_paper(arrays, _book(cfg, thr, ex, syms), _days(arrays, start, end), SimulatedBroker(COST),
                   cost_model=COST)
    paper = pt.closed_trades()
    research = TradeCache(arrays, "r", cost_model=COST)
    last = _days(arrays, start, end)[-1]
    n = 0
    for s in syms:
        r = research.trades(s, EntrySpec(method, thr), ex)
        first_exit = r.filter(pl.col("exit_date") >= start)["exit_date"].min()      # research is flat after it
        r = r.filter((pl.col("signal_date") >= first_exit) & (pl.col("exit_date") < last)).sort("signal_date")
        p = paper.filter((pl.col("symbol") == s) & (pl.col("signal_date") >= first_exit)
                         & (pl.col("exit_date") < last)).sort("signal_date")
        assert p["signal_date"].to_list() == r["signal_date"].to_list()
        assert p["entry_date"].to_list() == r["entry_date"].to_list()
        assert p["exit_date"].to_list() == r["exit_date"].to_list()
        assert np.allclose(p["entry_px"], r["entry_px"]) and np.allclose(p["exit_px"], r["exit_px"])
        assert np.allclose(p["net_pnl"] / p["shares"], r["net_pnl"] / r["shares"])        # same per-share pnl
        n += len(r)
    assert n > 10
    assert all(x["reconciled"] for x in pt.log)


def test_plan_uses_only_data_known_at_the_close(mkt):
    arrays, syms = mkt
    cfg = LadderConfig(method="rsi")
    ex = ExitSpec("prev_high", 0, 5)
    book = _book(cfg, 20.0, ex, syms)
    sig = TradeCache(arrays, "r").trades("S001", EntrySpec("rsi", 20.0), ex)["signal_date"]
    day = sig.filter(sig > date(2013, 3, 1))[0]
    a = plan_day(TradeCache(arrays_upto(arrays, day), "k"), book, day, {})
    x = arrays["S001"]
    k = int(np.searchsorted(x.dates, np.datetime64(day))) + 1
    y = replace(x, sig_close=np.r_[x.sig_close[:k], x.sig_close[k:] * 1.3],
                ex_open=np.r_[x.ex_open[:k], x.ex_open[k:] * 0.7])       # the future is different
    b = plan_day(TradeCache(arrays_upto({**arrays, "S001": y}, day), "k2"), book, day, {})
    assert [o.order_id for o in a.orders] == [o.order_id for o in b.orders]
    assert any(o.symbol == "S001" and o.intent == "open" for o in a.orders)


def test_netting_crosses_opposite_rows_and_allocates_fills():
    d = date(2020, 1, 2)
    orders = [Order("a", "R1", "X", 100, "open", d, 10.0), Order("b", "R2", "X", -60, "open", d, 10.0),
              Order("c", "R2", "Y", -5, "open", d, 20.0)]
    nets = net_orders(orders)
    assert [(n.symbol, n.qty) for n in nets] == [("X", 40), ("Y", -5)]
    br = SimulatedBroker(CostModel.flat(10.0))
    fills = br.execute(nets, {"X": 11.0, "Y": 21.0}, d)
    rfs = {rf.order.order_id: rf for rf in allocate(nets, fills, orders, {"X": 11.0, "Y": 21.0}, d)}
    assert rfs["b"].crossed and rfs["b"].commission == 0 and rfs["b"].price == 11.0
    assert np.isclose(rfs["a"].commission, 40 * 11.0 * 10 / 1e4)
    books = {"R1": RowBook("R1"), "R2": RowBook("R2")}
    for rf in rfs.values():
        books[rf.order.row].apply(rf)
    assert reconcile(books.values(), br.positions())["ok"]
    fully = net_orders([Order("p", "R1", "Z", 10, "open", d, 5.0), Order("q", "R2", "Z", -10, "open", d, 5.0)])
    assert fully[0].qty == 0 and br.execute(fully, {"Z": 5.0}, d) == {}


def test_reconciliation_flags_a_manual_broker_trade():
    d = date(2020, 1, 2)
    br = SimulatedBroker()
    b = RowBook("R1")
    o = Order("a", "R1", "X", 10, "open", d, 1.0)
    nets = net_orders([o])
    for rf in allocate(nets, br.execute(nets, {"X": 1.0}, d), [o], {"X": 1.0}, d):
        b.apply(rf)
    assert reconcile([b], br.positions())["ok"]
    br.net["Y"] = 3.0                                     # someone traded Y by hand
    rep = reconcile([b], br.positions())
    assert not rep["ok"] and rep["diffs"]["Y"]["diff"] == 3.0


def test_capacity_and_exit_only_rows(mkt):
    arrays, syms = mkt
    cfg = LadderConfig(method="ibs", max_positions=2, max_new_per_day=1)
    ex = ExitSpec("time", 3, 3)
    book = _book(cfg, 0.3, ex, syms)
    pt = run_paper(arrays, book, _days(arrays, date(2012, 1, 2), date(2012, 9, 1)), SimulatedBroker())
    per_day = pt.closed_trades().group_by("entry_date").len()["len"]
    assert per_day.max() <= 1 and max(len(b.positions) for b in pt.books.values()) <= 2
    day = date(2012, 9, 4)
    plan = plan_day(TradeCache(arrays_upto(arrays, day), "k"), book, day, pt.books, exit_only={cfg.rid})
    assert all(o.intent == "close" for o in plan.orders)


def test_paper_dividends_and_swap_match_research():
    bars, divs, _ = make_market(6, 1200, seed=9, kind="mean_revert", dividends=True, listings=False)
    bars = add_adj_factor(bars, divs)
    arrays = prepare_arrays(bars, divs)
    ex = ExitSpec("time", 10, 10)                        # long holds make ex-dates inside trades likely
    cfg = LadderConfig(method="rsi", exits=(ex,))
    start, end = date(2012, 6, 1), date(2013, 12, 31)
    cm = CostModel(SymbolCost(0.0, 2.0, 0.0, -7.2, -3.6))    # with swap
    pt = run_paper(arrays, _book(cfg, 25.0, ex, sorted(arrays)), _days(arrays, start, end), SimulatedBroker(cm),
                   cost_model=cm)
    paper = pt.closed_trades()
    research = TradeCache(arrays, "r", cost_model=cm)
    last = _days(arrays, start, end)[-1]
    with_div = 0
    for s in sorted(arrays):
        r = research.trades(s, EntrySpec("rsi", 25.0), ex)
        first_exit = r.filter(pl.col("exit_date") >= start)["exit_date"].min()
        r = r.filter((pl.col("signal_date") >= first_exit) & (pl.col("exit_date") < last)).sort("signal_date")
        p = paper.filter((pl.col("symbol") == s) & (pl.col("signal_date") >= first_exit)
                         & (pl.col("exit_date") < last)).sort("signal_date")
        assert p["signal_date"].to_list() == r["signal_date"].to_list()
        assert np.allclose(p["dividends"] / p["shares"], r["dividends"] / r["shares"])
        assert np.allclose(p["financing"] / p["shares"], r["financing"] / r["shares"])
        assert np.allclose(p["net_pnl"] / p["shares"], r["net_pnl"] / r["shares"])
        with_div += int((r["dividends"] != 0).sum())
    assert with_div >= 3
