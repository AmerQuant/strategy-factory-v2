"""Intraday paper / live job (docs/spec/daily.md, "Intraday"): the daily job's state and planner, bar by bar.

    uv run python scripts/run_intraday.py --init --state s.json --policy holdout.json --first-dp 2026-10-01 \\
        [--dp-months 6 --is-years 3]
    uv run python scripts/run_intraday.py --state s.json --store <store> --timeframe 1H \\
        [--resample 4h --clock-shift ny] [--broker sim|mt5 ...] [--skip-last] [--until 2026-10-02T20:00]

Every run processes all bars after the state's last bar (catch-up), saving the state after each one:
- sim: each bar's pending orders fill at its open, then the plan is made at its close (the research timing);
- mt5: run right after a bar closes; the plan at its close is executed at once at market. More than one new bar
  is refused (missed bars cannot be executed at their historical prices): run it on every bar, or re-initialise.
`--skip-last` drops the store's last bar when the data refresh also writes the bar that is still forming.
Bars, resampling and clock shift must be the same as in the research run that produced the policy.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel, load_cost_overrides
from sfactory.data.adjust import add_adj_factor
from sfactory.data.contracts import DIVIDENDS_SCHEMA
from sfactory.data.regime import market_up_series
from sfactory.data.resample import check_no_straddle, resample_bars, shift_clock
from sfactory.data.sfac_store import load_store
from sfactory.engine.cache import prepare_arrays
from sfactory.forward import alerts
from sfactory.forward.daily import DailyState, _d, bar_step, new_state

try:
    from run_daily import guard, policy_rows
except ImportError:                                   # imported as scripts.run_intraday
    from scripts.run_daily import guard, policy_rows


def parse(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--policy")
    ap.add_argument("--first-dp")
    ap.add_argument("--dp-months", type=int, default=6)
    ap.add_argument("--is-years", type=int, default=3)
    ap.add_argument("--store")
    ap.add_argument("--timeframe", default="1H")
    ap.add_argument("--resample", help="build bars of this length from --timeframe, e.g. 4h")
    ap.add_argument("--clock-shift", default="0h", help="as in research, e.g. ny")
    ap.add_argument("--membership")
    ap.add_argument("--dividends")
    ap.add_argument("--costs", default="moneta")
    ap.add_argument("--broker", choices=("sim", "mt5"), default="sim")
    ap.add_argument("--until", help="process bars up to this timestamp (default: the store's last bar)")
    ap.add_argument("--skip-last", action="store_true", help="ignore the store's last bar (still forming)")
    ap.add_argument("--report-dir")
    ap.add_argument("--mt5-login", type=int)
    ap.add_argument("--mt5-server")
    ap.add_argument("--symbol-map")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--kill-file", help="kill switch file (default: SF_KILL_FILE, set by the dashboard / scheduler)")
    ap.add_argument("--events-file", help="event log for alerts (default: SF_EVENTS_FILE)")
    return ap.parse_args(argv)


def new_bars(bars: pl.DataFrame, last: str | None, until: datetime | None, skip_last: bool) -> list[datetime]:
    """Bar timestamps still to process: after the state's last bar, up to `until`."""
    ts = bars["date"].unique().sort()
    if skip_last and len(ts):
        ts = ts[:-1]
    if last is not None:
        lt = _d(last)
        lt = lt if isinstance(lt, datetime) else datetime(lt.year, lt.month, lt.day)  # noqa: DTZ001
        ts = ts.filter(ts > lt)
    if until is not None:
        ts = ts.filter(ts <= until)
    return ts.to_list()


def main(argv=None) -> dict:
    a = parse(argv)
    if a.init:
        if not (a.policy and a.first_dp):
            raise SystemExit("--init needs --policy and --first-dp")
        st = new_state(policy_rows(a.policy), date.fromisoformat(a.first_dp), a.dp_months, a.is_years)
        st.save(a.state)
        return {"init": a.state, "rows": len(st.policy)}
    st = DailyState.load(a.state)
    load = load_store(a.store, a.timeframe)
    raw, version = load.bars, load.version
    raw = resample_bars(raw, a.resample, a.clock_shift) if a.resample else shift_clock(raw, a.clock_shift)
    check_no_straddle(raw, a.resample or a.timeframe.lower())
    if a.resample or a.clock_shift != "0h":
        version = f"{version}-{a.resample or a.timeframe}-shift{a.clock_shift}"
    divs = (pl.read_parquet(a.dividends).select(list(DIVIDENDS_SCHEMA)) if a.dividends
            else pl.DataFrame(schema=DIVIDENDS_SCHEMA))
    bars = add_adj_factor(raw, divs)
    mem = pl.read_parquet(a.membership) if a.membership else None
    costs = (CostModel.moneta_share_cfd_proxy() if a.costs == "moneta" else CostModel.flat(5.0) if a.costs == "flat5"
             else load_cost_overrides(a.costs, CostModel.moneta_share_cfd_proxy()))
    todo = new_bars(bars, st.last_day, datetime.fromisoformat(a.until) if a.until else None, a.skip_last)
    live = a.broker == "mt5"
    if live and len(todo) > 1:
        raise SystemExit(f"{len(todo)} new bars since {st.last_day} ({todo[0]} .. {todo[-1]}): the live job must "
                         "run after every bar; missed bars cannot be executed at their historical prices")
    if live:
        from sfactory.broker.mt5 import MT5Broker
        from sfactory.costs.convert import read_broker_map
        broker = MT5Broker(symbol_map=read_broker_map(a.symbol_map) if a.symbol_map else None, dry_run=a.dry_run)
        broker.connect(a.mt5_login, os.environ.get("SF_MT5_PASSWORD"), a.mt5_server)
    else:
        broker = SimulatedBroker(costs)
        broker.net = dict(st.sim_net)
    arrays = prepare_arrays(bars, divs)
    regime = (*market_up_series(bars), "eqw-ma200")        # causal: flags at t use bars up to t only
    reports = []
    for bar in todo:
        halt = guard(st, bar, None, a, "run_intraday")                  # the switch is re-read before every bar
        rep = bar_step(st, arrays, bars.filter(pl.col("date") <= bar), mem, bar, broker, live=live, cost_model=costs,
                       regime=regime, data_version=version, halt=halt)
        if rep.get("reconciled") is False:                               # now in the log: warn and re-check at once
            alerts.emit(alerts.events_file(a.events_file), "warning", "run_intraday",
                        f"{bar}: book and broker positions differ", state=a.state)
            guard(st, bar, None, a, "run_intraday")
        st.save(a.state)                                    # restartable after every bar
        reports.append(rep)
        if a.report_dir:
            Path(a.report_dir).mkdir(parents=True, exist_ok=True)
            name = str(np.datetime64(bar, "m")).replace(":", "")
            (Path(a.report_dir) / f"{name}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    out = {"bars": len(todo), "last_bar": st.last_day, "orders": sum(r["orders"] for r in reports),
           "reconciled": all(r["reconciled"] for r in reports), "data": version}
    print(json.dumps(out, default=str))
    return out


if __name__ == "__main__":
    main()
