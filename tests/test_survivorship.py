from datetime import date

import polars as pl

from sfactory.data.survivorship import listing_profile, survivorship_report
from sfactory.data.synthetic import make_market


def test_store_with_delistings_is_recognised():
    bars, _, _ = make_market(20, 1500, seed=5, kind="mean_revert", dividends=False, listings=True)
    rep = survivorship_report(bars)
    assert rep["verdict"] == "delistings_present" and rep["ended_early"] >= 3 and rep["started_late"] >= 3
    assert rep["examples_ended"] and rep["ended_by_year"]


def test_survivor_only_store_is_flagged():
    bars, _, _ = make_market(20, 1500, seed=5, kind="mean_revert", dividends=False, listings=False)
    rep = survivorship_report(bars)
    assert rep["verdict"] == "likely_survivor_only" and rep["ended_early"] == 0
    short = survivorship_report(bars.filter(pl.col("date") < date(2011, 6, 1)))
    assert short["verdict"] == "too_short"


def test_membership_names_the_leaks():
    bars, _, _ = make_market(10, 1500, seed=5, kind="mean_revert", dividends=False, listings=False)
    mem = pl.DataFrame({"symbol": ["S000", "S001", "GONE"],
                        "start": [date(2010, 1, 1), date(2010, 1, 1), date(2011, 1, 1)],
                        "end": [None, None, date(2013, 1, 1)]}, schema_overrides={"end": pl.Date})
    cut = bars.filter(~((pl.col("symbol") == "S001") & (pl.col("date") > date(2012, 1, 1))))   # data stops early
    rep = survivorship_report(cut, mem)
    m = rep["membership"]
    assert rep["verdict"] == "membership_gaps" and m["examples_without_bars"] == ["GONE"]
    assert m["members_with_bars_ending_before_membership"] == 1 and m["examples_short"][0]["symbol"] == "S001"


def test_profile_finds_gaps():
    bars, _, _ = make_market(3, 600, seed=1, kind="mean_revert", dividends=False, listings=False)
    holey = bars.filter(~((pl.col("symbol") == "S002") & pl.col("date").is_between(date(2011, 1, 1), date(2011, 3, 31))))
    p = listing_profile(holey).filter(pl.col("symbol") == "S002")
    assert p["max_gap_days"][0] > 60 and survivorship_report(holey, min_years=1)["long_gaps"] == 1


def test_script_reads_the_v1_store(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import check_survivorship
    bars, _, _ = make_market(8, 1200, seed=2, kind="mean_revert", dividends=False, listings=True)
    store, cat = tmp_path / "store", []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():     # the v1 store layout
        h = f"{sym:0>64}"
        (store / "alpaca_sip" / sym / "1D").mkdir(parents=True)
        g.select(pl.col("date").cast(pl.Datetime("us", "UTC")).alias("ts"), "open", "high", "low", "close",
                 "volume").write_parquet(store / "alpaca_sip" / sym / "1D" / f"{h}.parquet")
        cat.append({"source": "alpaca_sip", "symbol": sym, "asset_class": "us_equity", "timeframe": "1D",
                    "adjustment": "split", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"})
    pl.DataFrame(cat).write_parquet(store / "catalog.parquet")
    rep = check_survivorship.main(["--store", str(store), "--out", str(tmp_path / "s.json")])
    assert rep["verdict"] == "delistings_present" and (tmp_path / "s.json").exists()
