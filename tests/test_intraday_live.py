from datetime import datetime

import numpy as np
import polars as pl
import pytest

from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel
from sfactory.data.adjust import add_adj_factor
from sfactory.data.resample import broker_clock, check_no_straddle, resample_bars
from sfactory.data.synthetic import make_intraday_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.forward.live import BookEntry
from sfactory.forward.paper import _next_bar, run_paper
from sfactory.policy.ladder import LadderConfig
from sfactory.signals.methods import EntrySpec
from sfactory.signals.specs import ExitSpec

COST = CostModel.flat(1.0)


def dt(*a) -> datetime:
    return datetime(*a)  # noqa: DTZ001 - naive timestamps are the intraday convention


@pytest.fixture(scope="module")
def hourly():
    bars, divs, _ = make_intraday_market(5, 420, bars_per_day=7, seed=3, kind="mean_revert", mr_strength=0.3)
    bars = add_adj_factor(bars, divs)
    return prepare_arrays(bars, divs)


def test_broker_clock_follows_new_york_dst():
    b = pl.DataFrame({"symbol": ["X", "X"], "date": [dt(2024, 7, 10, 21), dt(2024, 1, 10, 22)]}).with_columns(
        pl.col("date").cast(pl.Datetime("us")))
    out = broker_clock(b)["date"].to_list()
    assert out == [dt(2024, 7, 11, 0), dt(2024, 1, 11, 0)]          # 17:00 New York -> 00:00, summer and winter
    h = pl.DataFrame({"symbol": "X", "date": [dt(2024, 7, 10, 13) + (dt(2024, 1, 1, i) - dt(2024, 1, 1))
                                              for i in range(24)],
                      "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}).with_columns(
        pl.col("date").cast(pl.Datetime("us")))
    four = resample_bars(h, "4h", "ny")
    check_no_straddle(four, "4h")
    assert (four["date"].dt.hour() % 4 == 0).all()


def test_session_close_exit_keeps_no_position_overnight(hourly):
    c = TradeCache(hourly, "i", cost_model=COST)
    ex = ExitSpec("none", 0, 250, trail_atr=3.0, flat_eod=True)
    assert ExitSpec("none", 0, 250, trail_atr=3.0).id + ":eod" == ex.id      # ids of existing exits unchanged
    t = c.trades("S000", EntrySpec("rsi", 20.0), ex)
    assert len(t) > 30
    same_day = (t["exit_date"].cast(pl.Date) == t["entry_date"].cast(pl.Date)) | t["forced_exit"]
    assert same_day.all()
    last = t["exit_date"].dt.hour().max()
    assert (t["entry_date"].dt.hour() < last).all()                 # never enters on the day's last bar


def test_next_bar_follows_the_session_calendar(hourly):
    d = hourly["S000"].dates
    day = d.astype("datetime64[D]")
    i_last = int(np.nonzero(day == day[70])[0][-1])                  # a session's last bar (20:00)
    assert _next_bar(d[: i_last]) == d[i_last]                       # inside the session: + one hour
    assert _next_bar(d[: i_last + 1]) == d[i_last + 1]               # after the close: next day's first bar


@pytest.mark.parametrize("ex", [ExitSpec("prev_high", 0, 5), ExitSpec("time", 4, 4, flat_eod=True)])
def test_intraday_paper_replay_reproduces_research(hourly, ex):
    cfg = LadderConfig(method="rsi", exits=(ex,))
    syms = sorted(hourly)
    d = hourly["S000"].dates
    bars_days = [x.astype(object) for x in d[(d >= np.datetime64("2010-07-01")) & (d < np.datetime64("2010-12-01"))]]
    book = {cfg.rid: BookEntry(cfg.rid, cfg, 15.0, ex.id, (), tuple(syms), {})}
    pt = run_paper(hourly, book, bars_days, SimulatedBroker(COST), cost_model=COST)
    paper = pt.closed_trades()
    research = TradeCache(hourly, "r", cost_model=COST)
    n = 0
    for s in syms:
        r = research.trades(s, EntrySpec("rsi", 15.0), ex)
        first_exit = r.filter(pl.col("exit_date") >= bars_days[0])["exit_date"].min()
        r = r.filter((pl.col("signal_date") >= first_exit) & (pl.col("exit_date") < bars_days[-1])).sort("signal_date")
        p = paper.filter((pl.col("symbol") == s) & (pl.col("signal_date") >= first_exit)
                         & (pl.col("exit_date") < bars_days[-1])).sort("signal_date")
        assert p["entry_date"].to_list() == r["entry_date"].to_list()
        assert p["exit_date"].to_list() == r["exit_date"].to_list()
        assert np.allclose(p["net_pnl"] / p["shares"], r["net_pnl"] / r["shares"])
        n += len(r)
    assert n > 20 and all(x["reconciled"] for x in pt.log)
