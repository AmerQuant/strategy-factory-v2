import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import polars as pl

from sfactory.costs.model import CostModel, SymbolCost, load_cost_overrides
from sfactory.data.adjust import add_adj_factor
from sfactory.data.audit import audit_bars, audit_summary
from sfactory.data.folds_from_data import fold_config_for
from sfactory.data.sfac_store import load_store, safe_component
from sfactory.data.synthetic import make_market
from sfactory.data.universe import eligible_top_liquidity
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.signals.methods import EntrySpec
from sfactory.signals.specs import NEUTRAL_MR_EXIT


def _write_v1_store(root: Path, bars: pl.DataFrame, bad: dict | None = None):
    """Write bars in the exact v1 layout: catalog.parquet + <source>/<symbol>/1D/<hash>.parquet."""
    rows = []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():
        h = f"{abs(hash(sym)):064d}"[-64:]
        d = root / "alpaca_sip" / safe_component(sym) / "1D"
        d.mkdir(parents=True, exist_ok=True)
        g.select(pl.col("date").cast(pl.Datetime("us", "UTC")).alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(d / f"{h}.parquet")
        q, adj = (bad or {}).get(sym, ("ok", "split"))
        rows.append({"source": "alpaca_sip", "symbol": sym, "asset_class": "us_equity", "timeframe": "1D",
                     "adjustment": adj, "snapshot_hash": h, "is_reference": True, "quality_status": q})
    pl.DataFrame(rows).write_parquet(root / "catalog.parquet")


def test_v1_store_adapter_reads_references_and_skips_bad(tmp_path):
    bars, _, _ = make_market(6, 400, seed=1, dividends=False, listings=False)
    _write_v1_store(tmp_path, bars, {"S001": ("critical", "split"), "S002": ("ok", "raw")})
    ld = load_store(tmp_path)
    assert ld.symbols == ["S000", "S003", "S004", "S005"]
    assert set(ld.skipped) == {"S001", "S002"} and ld.version.startswith("v1store-1D-")
    got = ld.bars.filter(pl.col("symbol") == "S000").sort("date")
    exp = bars.filter(pl.col("symbol") == "S000").sort("date")
    assert got["date"].to_list() == exp["date"].to_list() and np.allclose(got["close"], exp["close"])
    assert ld.caveats


def test_audit_flags_critical_and_warnings():
    d = [date(2020, 1, i) for i in range(1, 11)]
    b = pl.DataFrame({"symbol": ["A"] * 10 + ["B"] * 10, "date": d * 2,
                      "open": [10.0] * 20, "high": [11.0] * 20, "low": [9.0] * 20,
                      "close": [10.0] * 10 + [10, 10, 25, 10, 10, 10, 10, 10, 10, 10.0], "volume": [1.0] * 20})
    b = b.with_columns(pl.when((pl.col("symbol") == "B") & (pl.col("close") == 25)).then(26.0)
                       .otherwise(pl.col("high")).alias("high"))
    b = b.with_columns(pl.when((pl.col("symbol") == "A") & (pl.col("date") == d[3])).then(-1.0)
                       .otherwise(pl.col("low")).alias("low"))
    iss = audit_bars(b, stale_run=10)
    s = audit_summary(iss, 2)
    assert s["excluded_critical"] == ["A"]                      # negative low
    assert "price_spike" in s["issues"] and "stale_close" in s["issues"]


def test_top_liquidity_universe_is_point_in_time():
    bars, _, _ = make_market(10, 600, seed=2, listings=True)
    dp = date(2011, 6, 1)
    a = eligible_top_liquidity(dp, bars, top_n=4, min_history=100)
    pert = bars.with_columns(pl.when(pl.col("date") >= dp).then(pl.col("volume") * 1000).otherwise(pl.col("volume")))
    assert a == eligible_top_liquidity(dp, pert, top_n=4, min_history=100) and len(a) == 4


def test_swap_financing_is_charged_per_calendar_day():
    bars, divs, _ = make_market(3, 500, seed=3, dividends=False)
    bars = add_adj_factor(bars, divs)
    arr = prepare_arrays(bars, divs)
    base = TradeCache(arr, "v", cost_model=CostModel(SymbolCost(0, 1, 0)))
    sw = TradeCache(arr, "v", cost_model=CostModel(SymbolCost(0, 1, 0, -7.2, -3.6)))
    a = base.trades("S001", EntrySpec("rsi", 20), NEUTRAL_MR_EXIT)
    b = sw.trades("S001", EntrySpec("rsi", 20), NEUTRAL_MR_EXIT)
    days = (a["exit_date"] - a["entry_date"]).dt.total_days().to_numpy()
    expect = -(a["shares"] * a["entry_px"]).to_numpy() * 0.072 / 360 * days
    assert np.allclose(b["financing"].to_numpy(), expect) and np.allclose(b["net_pnl"] - a["net_pnl"], expect)
    assert CostModel.moneta_share_cfd_proxy().swap_pct("X", -1) == -3.5


def test_cost_overrides_csv(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("symbol,spread_bps,swap_long_pct\nAAPL,1.5,-5\n", encoding="utf-8")
    cm = load_cost_overrides(p, CostModel.moneta_share_cfd_proxy())
    base = CostModel.moneta_share_cfd_proxy().default          # missing columns fall back to the base default
    assert cm.per_side_bps("AAPL") == 0.75 + base.commission_bps + base.slippage_bps
    assert cm.swap_pct("AAPL", 1) == -5 and cm.swap_pct("MSFT", 1) == -6.88
    assert abs(CostModel.moneta_share_cfd_proxy().per_side_bps("X") - 11.6) < 1e-9   # the v1 Moneta proxy profile


def test_fold_config_from_span():
    c = fold_config_for(date(2005, 1, 3), date(2026, 7, 31))
    assert c.data_end == date(2026, 8, 1) and c.first_dp == date(2008, 2, 1)
    months = (c.data_end.year - c.holdout_start.year) * 12 + c.data_end.month - c.holdout_start.month
    assert months == round(259 * 0.2)


def test_run_real_end_to_end_on_a_fake_v1_store(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_real
    bars, _, _ = make_market(12, 2600, seed=11, kind="mean_revert", dividends=False, listings=False)
    store = tmp_path / "store"
    store.mkdir()
    _write_v1_store(store, bars)
    s = run_real.main(["--store", str(store), "--out", str(tmp_path / "run"), "--registry",
                       str(tmp_path / "reg.duckdb"), "--methods", "rsi,ma_cross", "--top-n", "8",
                       "--no-robustness", "--max-positions", "4", "--max-new", "2", "--open-holdout"])
    assert any(r.startswith("MR-RSI") for r in s["accepted"]) and s["holdout"] in ("pass", "fail")
    assert (tmp_path / "run" / "report.html").is_file() and (tmp_path / "run" / "evidence.json").is_file()
    s2 = run_real.main(["--store", str(store), "--out", str(tmp_path / "run2"), "--registry",
                        str(tmp_path / "reg.duckdb"), "--methods", "rsi", "--top-n", "8", "--no-robustness",
                        "--no-ensembles"])
    assert s2["trials"] == s["trials"] + 2          # the registry persists across runs
    assert datetime.now(UTC)                # keep timezone import used
