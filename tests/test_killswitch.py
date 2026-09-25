from datetime import date
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from sfactory.broker.orders import RowBook
from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel
from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import prepare_arrays
from sfactory.forward import alerts, killswitch
from sfactory.forward.daily import close_phase, daily_step, new_state, open_phase
from sfactory.policy.ladder import LadderConfig

COST = CostModel.flat(2.0)
CFG = LadderConfig(rung="A1", method="rsi", min_price=0.0, min_dollar_vol=0.0, min_is_trades=10)


def _state(pnls, days, log=()):
    b = RowBook("R")
    b.closed = [{"net_pnl": p, "exit_date": d} for p, d in zip(pnls, days)]
    return SimpleNamespace(books={"R": b}, log=list(log))


def test_limits_drawdown_daily_loss_and_reconciliation():
    lim = killswitch.Limits(capital=100_000, max_drawdown=0.1, max_daily_loss=0.02, max_recon_failures=2)
    ok = _state([5000, -1500], [date(2026, 1, 2), date(2026, 1, 5)])
    assert killswitch.breached(ok, lim, date(2026, 1, 5)) is None
    dd = _state([5000, -8000, -4000], [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)])
    assert "drawdown 12.0%" in killswitch.breached(dd, lim, date(2026, 1, 7))
    day = _state([1000, -2500], [date(2026, 1, 2), date(2026, 1, 5)])
    assert "loss today 2.5%" in killswitch.breached(day, lim, date(2026, 1, 5))
    assert killswitch.breached(day, lim, date(2026, 1, 6)) is None        # the loss was yesterday
    rec = _state([], [], log=[{"reconciled": False}, {"reconciled": True}, {"reconciled": False}])
    assert killswitch.breached(rec, lim, date(2026, 1, 5)) is None         # only one in a row
    assert "2 consecutive" in killswitch.breached(rec, lim, date(2026, 1, 5), reconciled_now=False)
    off = killswitch.Limits(max_drawdown=None, max_daily_loss=None, max_recon_failures=None)
    assert killswitch.breached(dd, off, date(2026, 1, 7)) is None


def test_trip_never_weakens_and_only_resume_releases(tmp_path):
    f = tmp_path / "k.json"
    assert not killswitch.load(f).active                                  # missing file: inactive
    killswitch.trip(f, "manual", by="me", mode="flatten")
    k = killswitch.trip(f, "limit", by="job", mode="halt_new")            # a job cannot weaken flatten
    assert k.active and k.mode == "flatten" and k.reason == "manual"
    k = killswitch.resume(f, by="me")
    assert not k.active and k.halt is None
    with pytest.raises(ValueError):
        killswitch.KillSwitch.from_json({"mode": "panic"})


@pytest.fixture(scope="module")
def mkt():
    bars, divs, mem = make_market(10, 1700, seed=12, kind="mean_revert", dividends=False, listings=False)
    bars = add_adj_factor(bars, divs)
    return bars, prepare_arrays(bars, divs), mem


def _run_until_positions(mkt):
    bars, arrays, mem = mkt
    st = new_state([CFG], first_dp=date(2013, 1, 1), data_start=date(2010, 1, 4))
    br = SimulatedBroker(COST)
    d = arrays["S000"].dates
    days = [x.astype(object) for x in d[(d >= np.datetime64("2013-01-02")) & (d < np.datetime64("2014-01-01"))]]
    for i, day in enumerate(days):
        daily_step(st, arrays, bars.filter(pl.col("date") <= day), mem, day, br, cost_model=COST)
        if sum(len(b.positions) for b in st.books.values()) >= 2 and not st.pending:
            return st, br, days[i + 1]
    raise AssertionError("no day with two open positions")


def test_halt_new_keeps_exits_and_flatten_closes_everything(mkt):
    bars, arrays, mem = mkt
    for mode in ("halt_new", "flatten"):
        st, br, nxt = _run_until_positions(mkt)
        held = {s for b in st.books.values() for s in b.positions}
        open_phase(st, arrays, nxt, br, COST)
        rep = close_phase(st, arrays, bars.filter(pl.col("date") <= nxt), mem, nxt, COST, halt=mode)
        opens = [o for o in st.pending if o.intent == "open"]
        closes = {o.symbol for o in st.pending if o.intent == "close"}
        assert rep["halt"] == mode and not opens
        if mode == "flatten":
            still = {s for b in st.books.values() for s in b.positions}
            assert closes == still and still                              # every position gets a close order
        else:
            assert closes <= held                                         # exits by their own rules only


def test_run_daily_trips_the_switch_on_a_breached_limit(tmp_path, monkeypatch):
    import json
    import sys
    from dataclasses import asdict
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_daily
    bars, _, _ = make_market(6, 1200, seed=4, kind="mean_revert", dividends=False, listings=False)
    store, cat = tmp_path / "store", []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():
        h = f"{sym:0>64}"
        (store / "alpaca_sip" / sym / "1D").mkdir(parents=True)
        g.select(pl.col("date").cast(pl.Datetime("us", "UTC")).alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(store / "alpaca_sip" / sym / "1D" / f"{h}.parquet")
        cat.append({"source": "alpaca_sip", "symbol": sym, "asset_class": "us_equity", "timeframe": "1D",
                    "adjustment": "split", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"})
    pl.DataFrame(cat).write_parquet(store / "catalog.parquet")
    row = LadderConfig(rung="A1", method="rsi", min_price=0.0, min_dollar_vol=0.0, min_is_trades=10,
                       universe_mode="top_liquidity", universe_top_n=6, min_history=200)
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"policy": {"rows": [asdict(row)]}}, default=str), encoding="utf-8")
    state, kfile, efile = tmp_path / "s.json", tmp_path / "kill.json", tmp_path / "events.jsonl"
    killswitch.save(kfile, killswitch.KillSwitch(limits=killswitch.Limits(capital=100_000, max_drawdown=1e-6)))
    monkeypatch.setenv("SF_KILL_FILE", str(kfile))
    monkeypatch.setenv("SF_EVENTS_FILE", str(efile))
    run_daily.main(["--init", "--state", str(state), "--policy", str(pol), "--first-dp", "2012-01-01"])
    days = [d for d in bars["date"].unique().sort().to_list() if date(2012, 1, 2) <= d < date(2012, 4, 1)]
    halts = []
    for d in days:
        rep = run_daily.main(["--state", str(state), "--store", str(store), "--costs", "flat5", "--day", str(d)])
        halts.append(rep["halt"])
    k = killswitch.load(kfile)
    assert k.active and k.mode == "halt_new" and "drawdown" in k.reason and k.by == "run_daily"
    first = halts.index("halt_new")
    assert all(h is None for h in halts[:first]) and all(h == "halt_new" for h in halts[first:])
    ev = alerts.read(efile)
    assert [e["level"] for e in ev].count("critical") == 1               # tripped once, not every day


def test_killswitch_api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from sfactory.web.app import create_app
    c = TestClient(create_app(tmp_path / "cfg", static_dir=tmp_path / "nodist", token=""))
    assert c.get("/api/killswitch").json()["active"] is False
    k = c.post("/api/killswitch/trip", json={"mode": "flatten", "reason": "test"}).json()
    assert k["active"] and k["mode"] == "flatten"
    assert c.post("/api/killswitch/trip", json={"mode": "nope"}).status_code == 422
    assert c.put("/api/killswitch/limits", json={"capital": 50_000, "max_drawdown": 0.1, "max_daily_loss": None,
                                                 "max_recon_failures": 3}).json()["limits"]["capital"] == 50_000
    assert c.put("/api/killswitch/limits", json={"capital": -1}).status_code == 422
    assert c.put("/api/killswitch/limits", json={"nope": 1}).status_code == 422
    assert c.post("/api/killswitch/resume").json()["active"] is False
    ev = c.get("/api/events").json()
    assert [e["level"] for e in ev] == ["info", "critical"]               # newest first
