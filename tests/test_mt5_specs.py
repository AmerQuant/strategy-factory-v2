import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import polars as pl
import pytest

from sfactory.broker.mt5_specs import enum_name, export_specs, read_specs_csv, write_specs_csv
from sfactory.data.sessions import bars_outside, read_sessions

RATES = np.dtype([("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                  ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])


class FakeSpecMT5:
    """The symbol_info / copy_rates surface of the MetaTrader5 package; constant values are arbitrary on purpose."""
    TIMEFRAME_H1 = 16385
    SYMBOL_SWAP_MODE_DISABLED, SYMBOL_SWAP_MODE_POINTS, SYMBOL_SWAP_MODE_INTEREST_CURRENT = 70, 71, 75
    SYMBOL_CALC_MODE_FOREX, SYMBOL_CALC_MODE_CFDINDEX = 40, 43

    def __init__(self):
        self.infos = {
            "EURUSD": NS(description="Euro vs Dollar", path="Forex\\Majors\\EURUSD", currency_base="EUR",
                         currency_profit="USD", currency_margin="EUR", digits=5, point=1e-5,
                         trade_contract_size=100_000.0, trade_tick_size=1e-5, trade_tick_value=1.0,
                         trade_tick_value_profit=1.0, trade_tick_value_loss=1.0, volume_min=0.01, volume_step=0.01,
                         volume_max=100.0, trade_calc_mode=40, swap_mode=71, swap_long=-7.1, swap_short=2.3,
                         swap_rollover3days=3, spread=9, spread_float=True),
            "US30": NS(description="Dow", path="Indices\\US30", currency_base="USD", currency_profit="USD",
                       currency_margin="USD", digits=2, point=0.01, trade_contract_size=1.0, trade_tick_size=0.01,
                       trade_tick_value=0.01, trade_tick_value_profit=0.01, trade_tick_value_loss=0.01,
                       volume_min=0.1, volume_step=0.1, volume_max=50.0, trade_calc_mode=43, swap_mode=75,
                       swap_long=-5.5, swap_short=1.25, swap_rollover3days=5, spread=250, spread_float=True)}
        t0 = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
        r = np.zeros(4, RATES)
        r["time"] = t0 + 3600 * np.arange(4)
        r["close"] = [1.10, 1.12, 1.08, 1.10]
        r["spread"] = [8, 10, 0, 12]
        self.rates = {"EURUSD": r}

    def account_info(self):
        return NS(currency="USD")

    def symbol_select(self, s, on):
        return s in self.infos

    def last_error(self):
        return (-1, "unknown symbol")

    def symbol_info(self, s):
        return self.infos.get(s)

    def copy_rates_from_pos(self, s, tf, start, count):
        assert tf == self.TIMEFRAME_H1
        return self.rates.get(s)

    def initialize(self, **kw):
        return True

    def shutdown(self):
        pass


def test_enum_names_come_from_the_package_constants():
    m = FakeSpecMT5()
    assert enum_name(m, "SYMBOL_SWAP_MODE_", 71) == "points"
    assert enum_name(m, "SYMBOL_CALC_MODE_", 43) == "cfdindex"
    assert enum_name(m, "SYMBOL_SWAP_MODE_", 99) == "unknown:99"


def test_export_reads_symbol_info_and_the_bar_spread(tmp_path):
    m = FakeSpecMT5()
    rows, skipped = export_specs(m, {"EURUSD": "EURUSD", "USA30IDXUSD": "US30", "GBPUSD": "GBPUSD"})
    assert set(skipped) == {"GBPUSD"} and "symbol_select" in skipped["GBPUSD"]
    eu = next(r for r in rows if r["research_symbol"] == "EURUSD")
    assert eu["swap_mode"] == "points" and eu["calc_mode"] == "forex" and eu["deposit_currency"] == "USD"
    assert eu["trade_contract_size"] == 100_000.0 and eu["swap_long"] == -7.1 and eu["swap_rollover3days"] == 3
    close, sp = np.array([1.10, 1.12, 1.08, 1.10]), np.array([8, 10, 0, 12.0])
    bps = sp * 1e-5 / close * 1e4
    assert eu["spread_bars"] == 4 and eu["spread_zero_bars"] == 1
    assert eu["spread_bps_mean"] == pytest.approx(bps.mean()) and eu["spread_bps_median"] == pytest.approx(
        np.median(bps))
    assert eu["spread_points_median"] == 9.0 and eu["ref_close_median"] == pytest.approx(1.10)
    us = next(r for r in rows if r["research_symbol"] == "USA30IDXUSD")
    assert us["spread_bars"] == 0 and us["swap_mode"] == "interest_current"          # no history on the terminal
    p = tmp_path / "specs.csv"
    write_specs_csv(rows, p)
    back = read_specs_csv(p)
    assert back["EURUSD"]["point"] == 1e-5 and back["EURUSD"]["swap_mode"] == "points"
    assert back["USA30IDXUSD"]["spread_bps_mean"] is None                            # empty stays empty


def test_export_script_with_a_fake_terminal(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import export_mt5_specs
    mp = tmp_path / "map.csv"
    mp.write_text("research_symbol,broker_symbol\nEURUSD,EURUSD\nUSA30IDXUSD,US30\n", encoding="utf-8")
    rep = export_mt5_specs.main(["--symbol-map", str(mp), "--out", str(tmp_path / "s.csv")], mt5=FakeSpecMT5())
    assert rep["exported"] == 2 and rep["no_spread_history"] == ["USA30IDXUSD"]
    assert set(read_specs_csv(tmp_path / "s.csv")) == {"EURUSD", "USA30IDXUSD"}


def test_sessions_file_is_validated_and_audits_bars(tmp_path):
    p = tmp_path / "sessions.csv"
    p.write_text("symbol,weekday,start,end\nA,mon,00:00,12:00\nA,mon,13:00,24:00\nA,tue,00:00,24:00\n",
                 encoding="utf-8")
    s = read_sessions(p)
    assert s["A"][0] == [(0, 720), (780, 1440)]
    ts = [datetime(2026, 9, 21, h) for h in (11, 12, 13)] + [datetime(2026, 9, 23, 5)]  # noqa: DTZ001
    bars = pl.DataFrame({"symbol": ["A"] * 4 + ["B"], "date": ts + [ts[0]]}).with_columns(
        pl.col("date").cast(pl.Datetime("us")))
    rep = bars_outside(bars, s)                                    # Mon 12:00 is the break
    assert rep == {"outside": {"A": 2}, "no_session": ["B"]}                           # the break and a Wednesday
    for bad in ("A,mon,10:00,09:00", "A,xyz,00:00,01:00", "A,mon,00:00,25:00"):
        p.write_text("symbol,weekday,start,end\n" + bad + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            read_sessions(p)
    p.write_text("symbol,weekday,start,end\nA,mon,00:00,10:00\nA,mon,09:00,12:00\n", encoding="utf-8")
    with pytest.raises(ValueError, match="overlapping"):
        read_sessions(p)
