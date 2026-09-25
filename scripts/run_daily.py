"""Daily paper / live job (docs/spec/daily.md).

    # once: create the state from the frozen policy (holdout result or evidence JSON, or a list of row configs)
    uv run python scripts/run_daily.py --init --state D:/sf2_live/state.json --policy holdout.json --first-dp 2026-10-01

    # paper, every trading day after the close (fills yesterday's plan at today's open from the bars, then plans)
    uv run python scripts/run_daily.py --state D:/sf2_live/state.json --store <store> --broker sim

    # live MT5: at the open (market fills), and after the close (plan)
    set SF_MT5_PASSWORD=...
    uv run python scripts/run_daily.py --state ... --store <store> --broker mt5 --phase open --mt5-login 123 --mt5-server Moneta-Demo --symbol-map broker_symbols.csv [--dry-run]
    uv run python scripts/run_daily.py --state ... --store <store> --broker mt5 --phase close

Every run writes <report-dir>/<day>-<phase>.json; the state file is replaced atomically.

FX / index / metal CFDs: `--asset-class fx|index_cfd|metal` builds daily bars on the 17:00 New York broker day from the
store's 1H snapshots (data/markets.py), uses static membership and needs `--costs` as a cost CSV; symbols without a
cost row are left out (listed in the report). Run it after 17:00 New York.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

import polars as pl

from sfactory.broker.sim import SimulatedBroker
from sfactory.costs.model import CostModel, load_cost_overrides, load_cost_table
from sfactory.data.adjust import add_adj_factor
from sfactory.data.contracts import DIVIDENDS_SCHEMA
from sfactory.data.markets import ASSET_CLASSES, CFD_CLASSES, load_market
from sfactory.data.regime import market_up_series
from sfactory.data.sfac_store import load_store
from sfactory.data.universe import static_membership
from sfactory.engine.cache import prepare_arrays
from sfactory.forward import alerts, killswitch
from sfactory.forward.daily import DailyState, close_phase, new_state, open_phase
from sfactory.forward.paper import arrays_upto


def policy_rows(path: str) -> list:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(d, dict) and "policy" in d:
        d = d["policy"]
    if isinstance(d, dict) and "holdout" in d and isinstance(d["holdout"], dict) and "policy" in d["holdout"]:
        d = d["holdout"]["policy"]
    rows = d["rows"] if isinstance(d, dict) else d
    if not rows:
        raise SystemExit("the policy has no rows")
    return rows


def guard(st: DailyState, day, reconciled_now, a, source: str) -> str | None:
    """Kill switch before planning: trip it on a breached limit, report mismatches; returns the halt mode."""
    kpath, epath = killswitch.kill_file(a.kill_file), alerts.events_file(a.events_file)
    k = killswitch.load(kpath)
    if reconciled_now is False:
        alerts.emit(epath, "warning", source, f"{day}: book and broker positions differ", state=a.state)
    reason = killswitch.breached(st, k.limits, day, reconciled_now)
    if reason and not k.active:
        if kpath is not None:
            k = killswitch.trip(kpath, reason, by=source)
        else:
            k.active, k.reason = True, reason
        alerts.emit(epath, "critical", source, f"kill switch tripped: {reason}", state=a.state, mode=k.mode)
    return k.halt


def parse(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--policy")
    ap.add_argument("--first-dp")
    ap.add_argument("--dp-months", type=int, default=6)
    ap.add_argument("--is-years", type=int, default=3)
    ap.add_argument("--store")
    ap.add_argument("--membership")
    ap.add_argument("--dividends")
    ap.add_argument("--costs", default="moneta")
    ap.add_argument("--broker", choices=("sim", "mt5"), default="sim")
    ap.add_argument("--phase", choices=("both", "open", "close"), default=None,
                    help="default: both for sim, required for mt5")
    ap.add_argument("--day", help="YYYY-MM-DD (default: the last day in the store)")
    ap.add_argument("--report-dir")
    ap.add_argument("--mt5-login", type=int)
    ap.add_argument("--mt5-server")
    ap.add_argument("--symbol-map")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--asset-class", default="us_equity", choices=ASSET_CLASSES)
    ap.add_argument("--kill-file", help="kill switch file (default: SF_KILL_FILE, set by the dashboard / scheduler)")
    ap.add_argument("--events-file", help="event log for alerts (default: SF_EVENTS_FILE)")
    return ap.parse_args(argv)


def main(argv=None) -> dict:
    a = parse(argv)
    if a.init:
        if not (a.policy and a.first_dp):
            raise SystemExit("--init needs --policy and --first-dp")
        st = new_state(policy_rows(a.policy), date.fromisoformat(a.first_dp), a.dp_months, a.is_years)
        st.save(a.state)
        return {"init": a.state, "rows": len(st.policy)}
    st = DailyState.load(a.state)
    divs = (pl.read_parquet(a.dividends).select(list(DIVIDENDS_SCHEMA)) if a.dividends
            else pl.DataFrame(schema=DIVIDENDS_SCHEMA))
    no_cost: list = []
    if a.asset_class in CFD_CLASSES:
        if a.costs in ("moneta", "flat5") or a.membership or a.dividends:
            raise SystemExit(f"{a.asset_class}: --costs must be a cost CSV; no --membership / --dividends")
        ml = load_market(a.store, a.asset_class, "1D")
        costs, priced = load_cost_table(a.costs)
        no_cost = sorted(set(ml.bars["symbol"].unique().to_list()) - priced)
        raw, version = ml.bars.filter(pl.col("symbol").is_in(sorted(priced))), ml.version
        mem = static_membership(raw)
    else:
        load = load_store(a.store, "1D")
        raw, version = load.bars, load.version
        mem = pl.read_parquet(a.membership) if a.membership else None
        costs = (CostModel.moneta_share_cfd_proxy() if a.costs == "moneta" else CostModel.flat(5.0)
                 if a.costs == "flat5" else load_cost_overrides(a.costs, CostModel.moneta_share_cfd_proxy()))
    bars = add_adj_factor(raw, divs)
    today = date.fromisoformat(a.day) if a.day else bars["date"].max()
    phase = a.phase or ("both" if a.broker == "sim" else None)
    if phase is None:
        raise SystemExit("--phase open|close is required with --broker mt5")
    done = st.last_day if phase in ("both", "close") else st.filled_day
    if not a.day and done is not None and done >= str(today):       # no new data (holiday, early rerun)
        out = {"day": str(today), "phase": phase, "noop": f"{today} already processed"}
        print(json.dumps(out))
        return out
    if a.broker == "sim":
        broker = SimulatedBroker(costs)
        broker.net = dict(st.sim_net)
    else:
        from sfactory.broker.mt5 import MT5Broker
        from sfactory.costs.convert import read_broker_map
        broker = MT5Broker(symbol_map=read_broker_map(a.symbol_map) if a.symbol_map else None, dry_run=a.dry_run)
        broker.connect(a.mt5_login, os.environ.get("SF_MT5_PASSWORD"), a.mt5_server)
    hist = bars.filter(pl.col("date") <= today)
    arrays = prepare_arrays(hist, divs)
    rep: dict = {"day": str(today), "phase": phase, "data": version}
    if no_cost:
        rep["symbols_without_costs"] = no_cost
    if phase in ("both", "open"):
        known = arrays if phase == "both" else arrays_upto(arrays, today)
        rep["open"] = open_phase(st, known, today, broker, costs)
        if hasattr(broker, "deal_costs"):                       # MT5: realised commission / fee / swap
            rep["broker_costs"] = broker.deal_costs(today)
    if phase in ("both", "close"):
        rep["halt"] = guard(st, today, rep.get("open", {}).get("reconciled"), a, "run_daily")
        regime = (*market_up_series(hist), "eqw-ma200")
        rep["close"] = close_phase(st, arrays, hist, mem, today, costs, regime, version, halt=rep["halt"])
        rec = rep.get("open", {"reconciled": None})
        st.log.append({"day": str(today), "reconciled": rec["reconciled"], "orders": rep["close"]["orders"],
                       "open_positions": rep["close"]["open_positions"]})
    st.save(a.state)
    if a.report_dir:
        Path(a.report_dir).mkdir(parents=True, exist_ok=True)
        (Path(a.report_dir) / f"{today}-{phase}.json").write_text(json.dumps(rep, indent=1, default=str),
                                                                  encoding="utf-8")
    print(json.dumps({"day": str(today), "phase": phase,
                      "orders": rep.get("close", {}).get("orders"),
                      "reconciled": rep.get("open", {}).get("reconciled")}, default=str))
    return rep


if __name__ == "__main__":
    main()
