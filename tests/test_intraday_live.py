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


@pytest.fixture(scope="module")
def hourly_job():
    bars, divs, mem = make_intraday_market(5, 560, bars_per_day=7, seed=11, kind="mean_revert", mr_strength=0.3)
    bars = add_adj_factor(bars, divs)
    return bars, prepare_arrays(bars, divs), mem


def _job(hourly_job, live: bool, restart_dir=None):
    from datetime import date

    from sfactory.forward.daily import DailyState, bar_step, new_state
    bars, arrays, mem = hourly_job
    cfg = LadderConfig(rung="A1", method="rsi", max_positions=0, min_price=0.0, min_dollar_vol=0.0,
                       min_is_trades=5, min_history=100)
    st = new_state([cfg], first_dp=date(2011, 1, 3), dp_months=6, is_years=1, data_start=date(2010, 1, 4))
    ts = arrays["S000"].dates
    run = ts[(ts >= np.datetime64("2011-01-03")) & (ts < np.datetime64("2011-02-15"))]
    br = SimulatedBroker(COST)
    for b in run:
        bar = b.astype("datetime64[us]").astype(object)
        if restart_dir is not None:
            st.save(restart_dir / "s.json")
            st = DailyState.load(restart_dir / "s.json")
            br.net = dict(st.sim_net)
        bar_step(st, arrays, bars.filter(pl.col("date") <= bar), mem, bar, br, live=live, cost_model=COST)
    return st, cfg, run


def test_intraday_job_reproduces_research_trades(hourly_job, tmp_path):
    _, arrays, _ = hourly_job
    st, cfg, run = _job(hourly_job, live=False, restart_dir=tmp_path)
    assert st.last_dp == "2011-01-03" and all(x["reconciled"] for x in st.log)
    be = st.book[cfg.rid]
    full = TradeCache(arrays, "full", cost_model=COST)
    ex = next(e for e in (cfg.neutral, *cfg.exit_lib) if e.id == be.exit_id)
    first, last = run[0], run[-1]
    want = []
    for s in be.eligible:
        t = full.trades(s, EntrySpec(cfg.method, be.threshold, cfg.direction), ex, be.filters)
        t = t.filter((pl.col("signal_date") >= first) & (pl.col("exit_date") <= last) & ~pl.col("forced_exit"))
        want += [(s, r["signal_date"], r["entry_date"], r["exit_date"]) for r in t.iter_rows(named=True)]
    got = st.closed_trades()
    got = [(r["symbol"], r["signal_date"], r["entry_date"], r["exit_date"]) for r in got.iter_rows(named=True)]
    assert len(want) > 20 and sorted(got) == sorted(want)
    assert all(isinstance(x[1], datetime) for x in got)          # bar timestamps survive the state file


def test_live_intraday_job_decides_at_the_same_bars(hourly_job):
    paper, _, run = _job(hourly_job, live=False)
    live, _, _ = _job(hourly_job, live=True)
    last = run[-1].astype("datetime64[us]").astype(object)

    def key(st):
        return sorted((r["symbol"], r["signal_date"], r["entry_date"], r["exit_date"])
                      for r in st.closed_trades().iter_rows(named=True) if r["exit_date"] <= last)
    assert len(key(paper)) > 20 and key(live) == key(paper)      # same decisions, same bars
    # live executes right after the bar closes, so it may already hold the exits of the bar after `last`
    assert all(r["exit_date"] > last for r in live.closed_trades().iter_rows(named=True)
               if (r["symbol"], r["signal_date"], r["entry_date"], r["exit_date"]) not in set(key(paper)))
    px = sorted(r["entry_px"] for r in live.closed_trades().iter_rows(named=True))
    assert px != sorted(r["entry_px"] for r in paper.closed_trades().iter_rows(named=True))   # fills at the close


def test_run_intraday_script_catches_up_and_refuses_live_gaps(tmp_path):
    import json
    import sys
    from dataclasses import asdict
    from pathlib import Path

    from sfactory.forward.daily import DailyState
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_intraday
    bars, _, _ = make_intraday_market(4, 420, bars_per_day=7, seed=5, kind="mean_revert", mr_strength=0.3)
    store, cat = tmp_path / "store", []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():     # the v1 store layout, hourly
        h = f"{sym:0>64}"
        (store / "alpaca_sip" / sym / "1H").mkdir(parents=True)
        g.select(pl.col("date").cast(pl.Datetime("us", "UTC")).alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(store / "alpaca_sip" / sym / "1H" / f"{h}.parquet")
        cat.append({"source": "alpaca_sip", "symbol": sym, "asset_class": "us_equity", "timeframe": "1H",
                    "adjustment": "split", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"})
    pl.DataFrame(cat).write_parquet(store / "catalog.parquet")
    row = LadderConfig(rung="A1", method="rsi", max_positions=0, min_price=0.0, min_dollar_vol=0.0,
                       min_is_trades=5, min_history=100, universe_mode="top_liquidity", universe_top_n=4)
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"policy": {"rows": [asdict(row)]}}, default=str), encoding="utf-8")
    state = tmp_path / "s.json"
    run_intraday.main(["--init", "--state", str(state), "--policy", str(pol), "--first-dp", "2011-01-03",
                       "--is-years", "1"])
    base = ["--state", str(state), "--store", str(store), "--costs", "flat5"]
    a = run_intraday.main([*base, "--until", "2011-01-20T20:00:00"])
    b = run_intraday.main([*base, "--until", "2011-02-10T20:00:00", "--report-dir", str(tmp_path / "rep")])
    again = run_intraday.main([*base, "--until", "2011-02-10T20:00:00"])
    st = DailyState.load(state)
    assert a["bars"] > 60 and b["bars"] > 60 and again["bars"] == 0 and a["reconciled"] and b["reconciled"]
    assert st.last_day == "2011-02-10 20:00:00" and st.last_dp == "2011-01-03" and len(st.closed_trades()) > 10
    assert len(list((tmp_path / "rep").glob("*.json"))) == b["bars"]
    with pytest.raises(SystemExit, match="new bars"):             # a live job that missed bars stops
        run_intraday.main([*base, "--broker", "mt5"])
