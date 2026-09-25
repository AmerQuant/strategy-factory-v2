import sys
from pathlib import Path

import pytest

from sfactory.costs.convert import convert_all, convert_profile, read_broker_map, resolve
from sfactory.costs.model import CostModel, load_cost_overrides

ROLL = {"rollover_time_local": "17:00", "rollover_tz": "America/New_York",
        "rollover_weekdays": ["MON", "TUE", "WED", "THU", "FRI"], "triple_weekday": "FRI"}
US_SHARE = {"name": "moneta_AAPL", "status": "verified", "broker_symbol": "AAPL",
            "spread": {"mode": "fixed", "fixed": {"value": 2.3, "unit": "bps"}}, "commission": {"model": "none"},
            "swap": {"model": "annual_rate", "long": -0.0688, "short": -0.035, "day_count": 360, **ROLL},
            "slippage": {"fixed": {"value": 1.0, "unit": "bps"}, "atr_fraction": 0.0}}
FX = {"name": "moneta_EURUSD", "status": "verified", "broker_symbol": "EURUSD", "pip_size": 0.0001,
      "spread": {"mode": "broker_scaled", "broker_spread": 0.00011},
      "commission": {"model": "per_lot", "lot_size": 100_000, "per_lot_per_side": 3.0, "currency": "USD"},
      "swap": {"model": "points_per_day", "long": -6.0, "short": 1.5, "point_size": 1e-5, **ROLL},
      "slippage": {"fixed": {"value": 0.2, "unit": "pip"}, "atr_fraction": 0.02}, "contract_size": 100_000}
PROXY = {"name": "us_share_cfd_proxy", "status": "placeholder",
         "spread": {"mode": "fixed", "fixed": {"value": 2.0, "unit": "bps"}}, "commission": {"model": "none"},
         "swap": {"model": "annual_rate", "long": -0.0688, "short": -0.035, "day_count": 360, **ROLL},
         "slippage": {"fixed": {"value": 1.0, "unit": "bps"}}}


def test_us_share_profile_needs_no_price():
    c, _ = convert_profile(US_SHARE)
    assert (c.spread_bps, c.commission_bps, c.slippage_bps) == (2.3, 0.0, 1.0)
    assert c.swap_long_pct == pytest.approx(-6.88) and c.swap_short_pct == pytest.approx(-3.5)
    assert c.per_side_bps == pytest.approx(2.3 / 2 + 1.0)


def test_fx_profile_price_units_pips_lots_points_and_atr():
    c, notes = convert_profile(FX, price=1.10, atr_pct=0.005)
    assert c.spread_bps == pytest.approx(0.00011 / 1.10 * 1e4)                       # 1.0 bps
    assert c.commission_bps == pytest.approx(3.0 / (100_000 * 1.10) * 1e4)           # per side
    assert c.slippage_bps == pytest.approx(0.2 * 0.0001 / 1.10 * 1e4 + 0.02 * 0.005 * 1e4)
    assert c.swap_long_pct == pytest.approx(-6.0 * 1e-5 / 1.10 * 360 * 100)
    assert c.swap_short_pct == pytest.approx(1.5 * 1e-5 / 1.10 * 360 * 100)
    assert any("ATR" in n for n in notes)


def test_other_models():
    per_share = {**PROXY, "commission": {"model": "per_share", "per_share": 0.005, "min_per_order": 1.0}}
    c, _ = convert_profile(per_share, price=50.0, notional=1_000.0)       # 20 shares -> 0.10 USD -> min 1 USD
    assert c.commission_bps == pytest.approx(1.0 / 1_000 * 1e4)
    per_order = {**PROXY, "commission": {"model": "per_order", "amount": 6.0, "currency": "USD"}}
    assert convert_profile(per_order, notional=12_000.0)[0].commission_bps == pytest.approx(5.0)
    cur = {**FX, "swap": {"model": "currency_per_lot_day", "long": -11.0, "short": 2.0, **ROLL},
           "slippage": {"fixed": {"value": 0.0}}}
    c, _ = convert_profile(cur, price=1.10)
    assert c.swap_long_pct == pytest.approx(-11.0 / (100_000 * 1.10) * 360 * 100)
    hourly = {**PROXY, "spread": {"mode": "hourly_profile", "hourly": [2.0] * 12 + [4.0] * 12, "unit": "bps"}}
    assert convert_profile(hourly)[0].spread_bps == pytest.approx(3.0)


def test_resolution_and_nothing_silent():
    profiles = {p["name"]: p for p in (US_SHARE, FX, PROXY)}
    groups = {"us_equity": "us_share_cfd_proxy"}
    assigned = {"AAPL": {"profile": "moneta_AAPL", "overrides": {"spread": {"fixed": {"value": 3.0}}}},
                "EURUSD": {"profile": "moneta_EURUSD"},
                "XYZ": {"profile": "moneta_AAPL",
                        "overrides": {"commission": {"model": "per_order", "amount": 1.0, "currency": "EUR"}}}}
    assert resolve("AAPL", "us_equity", profiles, groups, assigned)["spread"]["fixed"] == {"value": 3.0,
                                                                                          "unit": "bps"}
    rows, skipped = convert_all({"AAPL": "us_equity", "MSFT": "us_equity", "EURUSD": "fx", "XYZ": "us_equity",
                                 "BTC": "crypto"}, profiles, groups, assigned, prices={}, notional=10_000)
    got = {r.symbol: r for r in rows}
    assert set(got) == {"AAPL", "MSFT"} and got["MSFT"].status == "placeholder"
    assert "reference price" in skipped["EURUSD"] and "EUR" in skipped["XYZ"] and "no cost profile" in skipped["BTC"]


def test_script_end_to_end_feeds_the_v2_cost_model(tmp_path):
    yaml = pytest.importorskip("yaml")
    costs = tmp_path / "costs"
    (costs / "moneta").mkdir(parents=True)
    (costs / "us_share_cfd_proxy.yaml").write_text(yaml.safe_dump(PROXY), encoding="utf-8")
    (costs / "assignments.yaml").write_text(yaml.safe_dump({"groups": {"us_equity": "us_share_cfd_proxy"}}),
                                            encoding="utf-8")
    (costs / "moneta" / "moneta_profiles.yaml").write_text(yaml.safe_dump({"profiles": [US_SHARE, FX]}),
                                                           encoding="utf-8")
    (costs / "moneta" / "assignments.yaml").write_text(
        yaml.safe_dump({"symbols": {"AAPL": {"profile": "moneta_AAPL"}, "EURUSD": {"profile": "moneta_EURUSD"}}}),
        encoding="utf-8")
    uni = tmp_path / "u.csv"
    uni.write_text("symbol,asset_class\nAAPL,us_equity\nMSFT,us_equity\nEURUSD,fx\n", encoding="utf-8")
    px = tmp_path / "p.csv"
    px.write_text("symbol,price\nEURUSD,1.1\n", encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import convert_moneta_costs as conv
    out, mp = tmp_path / "c.csv", tmp_path / "m.csv"
    rep = conv.main(["--v1-costs", str(costs), "--universe", str(uni), "--prices", str(px), "--out", str(out),
                     "--map-out", str(mp)])
    assert rep["skipped"] == 1 and rep["converted"] == 2 and rep["placeholders"] == 1     # EURUSD: ATR missing
    cm = load_cost_overrides(out, CostModel.moneta_share_cfd_proxy())
    assert cm.per_side_bps("AAPL") == pytest.approx(2.3 / 2 + 1.0) and cm.swap_pct("AAPL", 1) == pytest.approx(-6.88)
    assert read_broker_map(mp) == {"AAPL": "AAPL"}
