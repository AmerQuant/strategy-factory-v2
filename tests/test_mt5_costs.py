import sys
from pathlib import Path

import pytest

from sfactory.broker.mt5_specs import write_specs_csv
from sfactory.costs.convert import read_broker_map
from sfactory.costs.model import CostModel, load_cost_overrides
from sfactory.costs.mt5_costs import convert_all_mt5, convert_symbol, read_manual, usd_notional_per_lot

# synthetic test fixtures (not real broker values)
EURUSD = {"research_symbol": "EURUSD", "broker_symbol": "EURUSD.ecn", "currency_base": "EUR",
          "currency_profit": "USD", "currency_margin": "EUR", "deposit_currency": "USD", "point": 1e-5,
          "trade_contract_size": 100_000.0, "calc_mode": "forex", "swap_mode": "points", "swap_long": -7.0,
          "swap_short": 2.0, "swap_rollover3days": 3.0, "spread_timeframe": "H1", "spread_bars": 2000.0,
          "spread_zero_bars": 0.0, "spread_bps_mean": 0.9, "spread_bps_median": 0.8, "ref_close_median": 1.10}
USDJPY = {**EURUSD, "research_symbol": "USDJPY", "broker_symbol": "USDJPY", "currency_base": "USD",
          "currency_profit": "JPY", "currency_margin": "USD", "point": 1e-3, "ref_close_median": 150.0,
          "swap_mode": "currency_deposit", "swap_long": 12.0, "swap_short": -20.0}
US30 = {**EURUSD, "research_symbol": "USA30IDXUSD", "broker_symbol": "US30", "currency_base": "USD",
        "currency_profit": "USD", "currency_margin": "USD", "point": 0.01, "trade_contract_size": 1.0,
        "calc_mode": "cfdindex", "swap_mode": "interest_current", "swap_long": -5.5, "swap_short": 1.25,
        "ref_close_median": 40_000.0, "spread_zero_bars": 3.0}
DAX = {**US30, "research_symbol": "DEUIDXEUR", "broker_symbol": "DE40", "currency_base": "EUR",
       "currency_profit": "EUR", "currency_margin": "EUR", "swap_mode": "currency_symbol"}
MAN = {"EURUSD": {"commission_per_lot_side": 3.0, "commission_currency": "USD", "slippage_bps": 0.2},
       "USDJPY": {"commission_per_lot_side": 3.0, "commission_currency": "USD", "slippage_bps": 0.3},
       "USA30IDXUSD": {"commission_per_lot_side": 0.0, "commission_currency": "", "slippage_bps": 0.5},
       "DEUIDXEUR": {"commission_per_lot_side": 0.0, "commission_currency": "", "slippage_bps": 0.5}}


def test_points_swap_and_per_lot_commission():
    c, notes, verify = convert_symbol(EURUSD, MAN["EURUSD"], "median")
    assert c.spread_bps == 0.8 and c.slippage_bps == 0.2 and not verify
    assert c.commission_bps == pytest.approx(3.0 / (100_000 * 1.10) * 1e4)
    assert c.swap_long_pct == pytest.approx(-7.0 * 1e-5 / 1.10 * 360 * 100)
    assert c.swap_short_pct == pytest.approx(2.0 * 1e-5 / 1.10 * 360 * 100)
    assert convert_symbol(EURUSD, MAN["EURUSD"], "mean")[0].spread_bps == 0.9
    assert any("triple swap" in n for n in notes)
    with pytest.raises(ValueError):
        convert_symbol(EURUSD, MAN["EURUSD"], "p90")


def test_usd_notional_and_currency_swaps():
    assert usd_notional_per_lot(EURUSD) == pytest.approx(110_000.0)          # profit currency USD
    assert usd_notional_per_lot(USDJPY) == 100_000.0                         # base USD, forex mode
    assert usd_notional_per_lot(DAX) is None                                 # EUR index: unknown in USD
    c, _, _ = convert_symbol(USDJPY, MAN["USDJPY"], "median")
    assert c.commission_bps == pytest.approx(3.0 / 100_000 * 1e4)
    assert c.swap_long_pct == pytest.approx(12.0 / 100_000 * 360 * 100)
    c, _, v = convert_symbol(US30, MAN["USA30IDXUSD"], "median")
    assert (c.swap_long_pct, c.swap_short_pct, c.commission_bps) == (-5.5, 1.25, 0.0) and v == ["zero_spread_bars"]
    assert "swap_interest_open_approx" in convert_symbol({**US30, "swap_mode": "interest_open"},
                                                         MAN["USA30IDXUSD"], "mean")[2]


def test_nothing_is_defaulted_silently():
    specs = {"EURUSD": EURUSD, "USDJPY": USDJPY, "USA30IDXUSD": US30, "DEUIDXEUR": DAX,
             "XAUUSD": {**US30, "research_symbol": "XAUUSD", "spread_bars": 0.0},
             "XAGUSD": {**US30, "research_symbol": "XAGUSD", "swap_mode": "reopen_current"},
             "GBPUSD": {**EURUSD, "research_symbol": "GBPUSD"}}
    manual = {**MAN, "XAUUSD": MAN["USA30IDXUSD"], "XAGUSD": MAN["USA30IDXUSD"]}
    rows, skipped = convert_all_mt5(specs, manual, "median", symbols=[*specs, "AUDUSD"])
    assert {r.symbol for r in rows} == {"EURUSD", "USDJPY", "USA30IDXUSD"}
    assert "EUR" in skipped["DEUIDXEUR"] and "spread history" in skipped["XAUUSD"]
    assert "reopen_current" in skipped["XAGUSD"] and "manual" in skipped["GBPUSD"]
    assert skipped["AUDUSD"] == "not in the MT5 export"
    no_slip = {**MAN["EURUSD"], "slippage_bps": None}
    assert "slippage" in convert_all_mt5({"EURUSD": EURUSD}, {"EURUSD": no_slip}, "mean")[1]["EURUSD"]
    eur_comm = {**MAN["EURUSD"], "commission_currency": "EUR"}
    assert "EUR" in convert_all_mt5({"EURUSD": EURUSD}, {"EURUSD": eur_comm}, "mean")[1]["EURUSD"]


def test_script_end_to_end_feeds_the_cost_model(tmp_path):
    specs = tmp_path / "specs.csv"
    write_specs_csv([EURUSD, US30, DAX], specs)
    man = tmp_path / "man.csv"
    man.write_text("symbol,commission_per_lot_side,commission_currency,slippage_bps\nEURUSD,3,USD,0.2\n"
                   "USA30IDXUSD,0,,0.5\nDEUIDXEUR,0,,0.5\n", encoding="utf-8")
    assert read_manual(man)["USA30IDXUSD"]["commission_per_lot_side"] == 0.0
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import convert_mt5_costs
    out, mp = tmp_path / "c.csv", tmp_path / "m.csv"
    rep = convert_mt5_costs.main(["--specs", str(specs), "--manual", str(man), "--spread-stat", "median",
                                  "--out", str(out), "--map-out", str(mp)])
    assert rep["converted"] == 2 and set(rep["skipped"]) == {"DEUIDXEUR"}
    cm = load_cost_overrides(out, CostModel())
    assert cm.per_side_bps("USA30IDXUSD") == pytest.approx(0.8 / 2 + 0.5) and cm.swap_pct("USA30IDXUSD", 1) == -5.5
    assert read_broker_map(mp) == {"EURUSD": "EURUSD.ecn", "USA30IDXUSD": "US30"}
    with pytest.raises(SystemExit):                                        # --spread-stat has no default
        convert_mt5_costs.main(["--specs", str(specs), "--manual", str(man), "--out", str(out)])
    bad = tmp_path / "bad.csv"
    bad.write_text("symbol,commission_per_lot_side\nEURUSD,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        read_manual(bad)
