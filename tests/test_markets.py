import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from sfactory.costs.model import load_cost_table
from sfactory.data.markets import load_market
from sfactory.data.resample import broker_clock
from sfactory.data.synthetic import make_intraday_market
from sfactory.policy.catalog import CFD_CATALOG_VERSION, cfd_rows, taxonomy
from sfactory.policy.ladder import LadderConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _dukascopy_store(root: Path, bars: pl.DataFrame, asset_class: str = "fx"):
    """The v1 layout of Dukascopy snapshots: 1H, adjustment raw, ts = bar start UTC."""
    cat = []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():
        h = f"{sym:0>64}"
        d = root / "dukascopy" / sym / "1H"
        d.mkdir(parents=True)
        g.select(pl.col("date").dt.replace_time_zone("UTC").alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(d / f"{h}.parquet")
        cat.append({"source": "dukascopy", "symbol": sym, "asset_class": asset_class, "timeframe": "1H",
                    "adjustment": "raw", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"})
    pl.DataFrame(cat).write_parquet(root / "catalog.parquet")


def _costs(path: Path, symbols, skip=()):
    rows = "".join(f"{s},1.0,0.5,0.2,-2.0,-1.0\n" for s in symbols if s not in skip)
    path.write_text("symbol,spread_bps,commission_bps,slippage_bps,swap_long_pct,swap_short_pct\n" + rows,
                    encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def fx_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("fxstore")
    bars, _, _ = make_intraday_market(6, 2600, bars_per_day=24, first_hour=0, seed=7, kind="mean_revert",
                                      mr_strength=0.3, bar_vol_scale=0.35)
    _dukascopy_store(root, bars)
    return root, bars


def test_market_loader_uses_the_new_york_broker_day(fx_store):
    root, bars = fx_store
    hourly = load_market(root, "fx", "1H")
    ref = broker_clock(bars)
    assert hourly.weekend_bars == ref.filter(pl.col("date").dt.weekday() >= 6).height > 0
    assert hourly.bars["date"].dt.weekday().max() <= 5 and hourly.bars.schema["date"] == pl.Datetime("us")
    assert hourly.bars.height == bars.height - hourly.weekend_bars and "ny-1H" in hourly.version
    daily = load_market(root, "fx", "1D")
    assert daily.bars.schema["date"] == pl.Date and daily.bars["date"].dt.weekday().max() <= 5
    one = daily.bars.filter(pl.col("symbol") == "S000").row(10, named=True)
    day_bars = hourly.bars.filter((pl.col("symbol") == "S000") & (pl.col("date").cast(pl.Date) == one["date"]))
    assert one["open"] == day_bars["open"][0] and one["close"] == day_bars["close"][-1]
    four = load_market(root, "fx", "1H", resample="4h")
    assert (four.bars["date"].dt.hour() % 4 == 0).all()
    with pytest.raises(ValueError, match="New York"):
        load_market(root, "fx", "1H", clock_shift="7h")
    assert load_market(root, "metal", "1D").bars.is_empty()               # wrong class: nothing read


def test_cost_table_is_strict(tmp_path):
    cm, syms = load_cost_table(_costs(tmp_path / "c.csv", ["A", "B"]))
    assert syms == {"A", "B"} and cm.per_side_bps("A") == pytest.approx(0.5 + 0.5 + 0.2)
    assert cm.per_side_bps("Z") != cm.per_side_bps("Z")                   # NaN: never a silent default
    (tmp_path / "bad.csv").write_text("symbol,spread_bps,commission_bps,slippage_bps,swap_long_pct,swap_short_pct\n"
                                      "A,1,0.5,,0,0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="slippage_bps"):
        load_cost_table(tmp_path / "bad.csv")


def test_cfd_catalogue():
    fx = cfd_rows("fx", 4, LadderConfig(rung="A1"))
    assert len(fx) == 18 and len({r.rid for r in fx}) == 18 and all(r.rid.endswith("-FX-TOP4") for r in fx)
    ix = cfd_rows("index_cfd", 3)
    mt = cfd_rows("metal", 0)
    assert ix[0].rid.endswith("-IX-TOP3") and mt[0].rid.endswith("-MT") and mt[0].symbol_select == 0
    assert all(r.min_price == 0 and r.min_dollar_vol == 0 for r in fx + ix + mt)
    assert {taxonomy(r)["asset_class"] for r in fx + ix + mt} == {"fx", "indices", "metals"}
    for bad in (("metal", 2), ("fx", 0), ("energy_cfd", 1)):
        with pytest.raises(ValueError):
            cfd_rows(*bad)
    assert CFD_CATALOG_VERSION


def test_run_real_on_fx_daily_bars(fx_store, tmp_path):
    import run_real
    root, bars = fx_store
    costs = _costs(tmp_path / "c.csv", sorted(bars["symbol"].unique()), skip=("S005",))
    base = ["--store", str(root), "--registry", str(tmp_path / "reg.duckdb"), "--asset-class", "fx",
            "--costs", str(costs)]
    s = run_real.main([*base, "--out", str(tmp_path / "run"), "--symbol-top-n", "3", "--methods", "rsi",
                       "--no-robustness", "--no-ensembles"])
    assert s["trials"] == 2
    ev = json.loads((tmp_path / "run" / "evidence.json").read_text(encoding="utf-8"))
    meta = ev["meta"]
    assert meta["asset_class"] == "fx" and meta["catalog"] == CFD_CATALOG_VERSION and meta["clock"] == "ny"
    assert meta["symbols_without_costs"] == ["S005"] and meta["survivorship"]["verdict"] == "not_applicable"
    assert {r["row"] for r in ev["rows"]} == {"MR-RSI-BUY-FX-TOP3", "MR-RSI-SELL-FX-TOP3"}
    dec = [d for r in ev["rows"] if r["fold_decisions"] for d in r["fold_decisions"]]
    assert all("S005" not in d["symbols"] for d in dec)                  # no cost row: never traded
    for bad in (["--costs", "moneta"], ["--diverse"], ["--clock-shift", "7h"]):
        with pytest.raises((SystemExit, ValueError)):
            run_real.main([*base, "--out", str(tmp_path / "x"), "--symbol-top-n", "3", *bad])
    with pytest.raises(ValueError, match="top_n"):
        run_real.main([*base, "--out", str(tmp_path / "y")])            # FX rows need --symbol-top-n


def test_run_daily_on_fx(fx_store, tmp_path):
    import run_daily

    from sfactory.forward.daily import DailyState
    root, bars = fx_store
    costs = _costs(tmp_path / "c.csv", sorted(bars["symbol"].unique()))
    row = cfd_rows("fx", 3, LadderConfig(rung="A1", min_is_trades=10))[0]
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps({"policy": {"rows": [asdict(row)]}}, default=str), encoding="utf-8")
    state = tmp_path / "s.json"
    run_daily.main(["--init", "--state", str(state), "--policy", str(pol), "--first-dp", "2013-01-01"])
    days = [d for d in load_market(root, "fx", "1D").bars["date"].unique().sort().to_list()
            if date(2013, 1, 2) <= d < date(2013, 2, 15)]
    for d in days:
        run_daily.main(["--state", str(state), "--store", str(root), "--asset-class", "fx", "--costs", str(costs),
                        "--day", str(d)])
    st = DailyState.load(state)
    assert st.last_dp == "2013-01-01" and len(st.log) == len(days) and all(x["reconciled"] for x in st.log)
    assert 1 <= len(st.book[row.rid].eligible) <= 3 and len(st.closed_trades()) > 0   # S7 keeps t >= 1 only
    with pytest.raises(SystemExit):
        run_daily.main(["--state", str(state), "--store", str(root), "--asset-class", "fx", "--costs", "moneta"])


def test_run_intraday_on_metals_4h(tmp_path):
    import run_intraday

    from sfactory.forward.daily import DailyState
    root = tmp_path / "store"
    bars, _, _ = make_intraday_market(2, 330, bars_per_day=24, first_hour=0, seed=3, kind="mean_revert",
                                      mr_strength=0.3, bar_vol_scale=0.35)
    _dukascopy_store(root, bars, "metal")
    costs = _costs(tmp_path / "c.csv", ["S000", "S001"])
    row = cfd_rows("metal", 0, LadderConfig(rung="A1", min_is_trades=5, min_history=100))[0]
    pol = tmp_path / "p.json"
    pol.write_text(json.dumps({"policy": {"rows": [asdict(row)]}}, default=str), encoding="utf-8")
    state = tmp_path / "s.json"
    run_intraday.main(["--init", "--state", str(state), "--policy", str(pol), "--first-dp", "2010-11-01",
                       "--is-years", "1"])
    out = run_intraday.main(["--state", str(state), "--store", str(root), "--asset-class", "metal",
                             "--resample", "4h", "--costs", str(costs), "--until", "2010-11-20T00:00:00"])
    st = DailyState.load(state)
    assert out["bars"] > 1000 and out["reconciled"] and st.last_dp == "2010-11-01"
    assert "metal-ny-4h" in out["data"] and set(st.book[row.rid].eligible) == {"S000", "S001"}
