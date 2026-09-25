from datetime import date

import numpy as np
import polars as pl
import pytest

from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel
from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import prepare_arrays
from sfactory.forward.daily import DailyState, close_phase, daily_step, due_dp, new_state, open_phase
from sfactory.forward.paper import arrays_upto
from sfactory.policy.ladder import LadderConfig

COST = CostModel.flat(2.0)
POLICY = [LadderConfig(rung="A1", method="rsi", min_price=0.0, min_dollar_vol=0.0, min_is_trades=10),
          LadderConfig(rung="A3", method="donchian_break", max_positions=3, max_new_per_day=2, min_price=0.0,
                       min_dollar_vol=0.0, min_is_trades=10)]


@pytest.fixture(scope="module")
def mkt():
    bars, divs, mem = make_market(10, 1700, seed=12, kind="mean_revert", dividends=False, listings=False)
    bars = add_adj_factor(bars, divs)
    return bars, prepare_arrays(bars, divs), mem


def _days(arrays, start, end):
    d = arrays["S000"].dates
    return [x.astype(object) for x in d[(d >= np.datetime64(start)) & (d < np.datetime64(end))]]


def _run(mkt, tmp_path=None, start=date(2013, 1, 2), end=date(2015, 9, 1)):
    bars, arrays, mem = mkt
    st = new_state(POLICY, first_dp=date(2013, 1, 1), dp_months=6, is_years=3, data_start=date(2010, 1, 4))
    reports = []
    for day in _days(arrays, start, end):
        if tmp_path is not None:                                    # the job restarts every day
            st.save(tmp_path / "state.json")
            st = DailyState.load(tmp_path / "state.json")
        br = SimulatedBroker(COST)
        br.net = dict(st.sim_net)
        reports.append(daily_step(st, arrays, bars.filter(pl.col("date") <= day), mem, day, br, cost_model=COST))
    return st, reports


def test_daily_job_is_restartable_and_consistent(mkt, tmp_path):
    a, ra = _run(mkt)
    b, _ = _run(mkt, tmp_path)
    ta, tb = a.closed_trades().sort(["row", "symbol", "signal_date"]), b.closed_trades().sort(
        ["row", "symbol", "signal_date"])
    assert len(ta) > 50 and ta.equals(tb)
    assert all(r["reconciled"] for r in ra)
    dps = [r["dp"]["dp"] for r in ra if "dp" in r]
    assert dps == ["2013-01-01", "2013-07-01", "2014-01-01", "2014-07-01", "2015-01-01", "2015-07-01"]
    assert all("no research trade" not in w for r in ra for w in r["warnings"])


def test_positions_exit_by_the_setting_they_were_opened_with(mkt):
    st, reports = _run(mkt)
    changed = [r for r in reports if "dp" in r and r["dp"]["changed"]]
    assert changed                                  # parameters did move at some DP
    assert all("setting" not in w and "no research trade" not in w for r in reports for w in r["warnings"])
    assert not st.opened_with.keys() - {(r, s) for r, b in st.books.items() for s in b.positions} - {
        (o.row, o.symbol) for o in st.pending}


def test_due_dp_and_guards(mkt):
    st = new_state(POLICY, first_dp=date(2013, 1, 1))
    assert due_dp(st, date(2012, 12, 31)) is None and due_dp(st, date(2013, 1, 2)) == date(2013, 1, 1)
    st.last_dp = "2013-01-01"
    assert due_dp(st, date(2013, 6, 28)) is None and due_dp(st, date(2014, 2, 3)) == date(2014, 1, 1)  # catch up
    bars, arrays, mem = mkt
    day = date(2013, 1, 2)
    br = SimulatedBroker(COST)
    daily_step(st, arrays, bars, mem, day, br)
    with pytest.raises(ValueError):
        daily_step(st, arrays, bars, mem, day, br)
    st.pending = st.pending or [object()]
    with pytest.raises(ValueError):
        close_phase(st, arrays, bars, mem, date(2013, 1, 3))


def test_open_phase_without_todays_bar_uses_reference_prices(mkt):
    bars, arrays, mem = mkt
    st = new_state(POLICY, first_dp=date(2013, 1, 1), data_start=date(2010, 1, 4))
    days = _days(arrays, date(2013, 1, 2), date(2013, 6, 1))
    br = SimulatedBroker(COST)
    i = 0
    while not st.pending:                                            # run until a close leaves orders to fill
        daily_step(st, arrays, bars, mem, days[i], br)
        i += 1
    refs = {o.symbol: o.ref_price for o in st.pending}
    rep = open_phase(st, arrays_upto(arrays, days[i - 1]), days[i], br)   # at the open: today's bar not there yet
    assert rep["reconciled"] and not st.pending
    assert all(f.price == refs[f.symbol] for f in br.fills[-len(refs):])


def test_run_daily_script_on_a_fake_v1_store(tmp_path):
    import json
    import sys
    from dataclasses import asdict
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_daily
    bars, _, _ = make_market(6, 1200, seed=4, kind="mean_revert", dividends=False, listings=False)
    store = tmp_path / "store"
    cat = []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():     # the v1 store layout
        h = f"{sym:0>64}"
        (store / "alpaca_sip" / sym / "1D").mkdir(parents=True)
        g.select(pl.col("date").cast(pl.Datetime("us", "UTC")).alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(store / "alpaca_sip" / sym / "1D" / f"{h}.parquet")
        cat.append({"source": "alpaca_sip", "symbol": sym, "asset_class": "us_equity", "timeframe": "1D",
                    "adjustment": "split", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"})
    pl.DataFrame(cat).write_parquet(store / "catalog.parquet")
    pol = tmp_path / "policy.json"
    row = LadderConfig(rung="A1", method="rsi", min_price=0.0, min_dollar_vol=0.0, min_is_trades=10,
                       universe_mode="top_liquidity", universe_top_n=6, min_history=200)
    pol.write_text(json.dumps({"policy": {"rows": [asdict(row)]}}, default=str), encoding="utf-8")
    state = tmp_path / "state.json"
    run_daily.main(["--init", "--state", str(state), "--policy", str(pol), "--first-dp", "2012-01-01"])
    days = [d for d in bars["date"].unique().sort().to_list() if date(2012, 1, 2) <= d < date(2012, 3, 1)]
    for d in days:
        run_daily.main(["--state", str(state), "--store", str(store), "--costs", "flat5", "--day", str(d),
                        "--report-dir", str(tmp_path / "rep")])
    st = DailyState.load(state)
    assert st.last_dp == "2012-01-01" and len(st.log) == len(days) and all(x["reconciled"] for x in st.log)
    assert len(st.closed_trades()) > 5 and len(list((tmp_path / "rep").glob("*.json"))) == len(days)
