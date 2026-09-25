"""v1 cost profiles (Strategy Factory `configs/costs`, T06/T06b Moneta build) -> v2 per-symbol cost CSV.

v2 prices costs in basis points of traded notional per side plus swap in % per year (`costs.model`); v1 profiles
use several units. The conversion per resolved profile (assignment overrides applied as v1 does):

| v1 | v2 |
|---|---|
| spread fixed bps / price / pip; hourly profile (mean of 24 h); broker_scaled (`broker_spread` price) | `spread_bps` (full spread; v2 charges half per side) |
| commission percent / per_share (min/max per order) / per_lot / per_order | `commission_bps` per side |
| slippage fixed (bps / price / pip) + atr_fraction x ATR% | `slippage_bps` per side |
| swap annual_rate (fraction, v1 T06b) / points_per_day / currency_per_lot_day | `swap_long_pct`, `swap_short_pct` (% per year, 360 days) |

Price-based units need a reference price per symbol (and per_order / per_share commissions a trade notional,
ATR slippage an ATR%); a symbol whose profile needs a value that is not supplied is NOT written - it is reported,
so a missing number never silently falls back to a default. Only USD-quoted costs are converted; others are
reported. `status: placeholder` profiles (the D-324 proxy) are converted but flagged in the `status` column.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

from sfactory.costs.model import SymbolCost

CSV_COLUMNS = ("symbol", "spread_bps", "commission_bps", "slippage_bps", "swap_long_pct", "swap_short_pct",
               "profile", "status", "broker_symbol", "ref_price", "to_verify", "notes")


class NeedsInput(ValueError):
    """The profile needs a reference value (price, notional, ATR%) that was not supplied."""


@dataclass
class Converted:
    symbol: str
    cost: SymbolCost
    profile: str
    status: str
    broker_symbol: str | None
    ref_price: float | None
    to_verify: tuple = ()
    notes: list = field(default_factory=list)


def merge(base: dict, over: dict) -> dict:
    """v1 `_merge`: nested dict override."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def resolve(symbol: str, asset_class: str, profiles: dict, groups: dict, symbols: dict) -> dict:
    """v1 `resolve_profile`: the symbol entry first, then its asset-class group; overrides merged."""
    entry = symbols.get(symbol)
    name = entry["profile"] if entry else groups.get(asset_class)
    if name is None:
        raise KeyError(f"no cost profile assigned to {symbol} ({asset_class})")
    prof = profiles[name]
    if entry and entry.get("overrides"):
        prof = merge(prof, entry["overrides"])
    return prof


def _need(v, what: str):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        raise NeedsInput(what)
    return v


def amount_bps(amount: dict, price: float | None, pip_size: float | None) -> float:
    unit, value = amount.get("unit", "price"), float(amount["value"])
    if value == 0:
        return 0.0
    if unit == "bps":
        return value
    if unit == "price":
        return value / _need(price, "reference price") * 1e4
    if unit == "pip":
        return value * _need(pip_size, "pip_size") / _need(price, "reference price") * 1e4
    raise ValueError(f"unknown unit {unit}")


def spread_bps(s: dict, price, pip_size) -> float:
    mode = s["mode"]
    if mode == "fixed":
        return amount_bps(s["fixed"], price, pip_size)
    if mode == "hourly_profile":
        mean = sum(s["hourly"]) / len(s["hourly"])
        return amount_bps({"value": mean, "unit": s.get("unit", "price")}, price, pip_size)
    if mode == "broker_scaled":
        return amount_bps({"value": s["broker_spread"], "unit": "price"}, price, pip_size)
    if mode == "from_data":
        if s.get("fallback") is None:
            raise NeedsInput("spread from_data without fallback (needs the v1 snapshot spread table)")
        return amount_bps(s["fallback"], price, pip_size) * float(s.get("scale", 1.0))
    raise ValueError(f"unknown spread mode {mode}")


def commission_bps(c: dict, price, notional) -> float:
    model = c.get("model", "none")
    if model != "none" and c.get("currency", "USD") != "USD":
        raise NeedsInput(f"commission in {c['currency']} (only USD converted)")
    if model == "none":
        return 0.0
    if model == "percent":
        return float(c["rate"]) * 1e4
    if model == "per_lot":
        return float(c["per_lot_per_side"]) / (float(c["lot_size"]) * _need(price, "reference price")) * 1e4
    if model == "per_order":
        return float(c["amount"]) / _need(notional, "trade notional") * 1e4
    if model == "per_share":
        n = _need(notional, "trade notional")
        shares = n / _need(price, "reference price")
        amt = max(float(c.get("min_per_order", 0.0)), float(c["per_share"]) * shares)
        if c.get("max_per_order") is not None:
            amt = min(amt, float(c["max_per_order"]))
        return amt / n * 1e4
    raise ValueError(f"unknown commission model {model}")


def swap_pct(s: dict, price, contract_size: float, quote_ccy: str) -> tuple[float, float]:
    model = s.get("model", "none")
    if model == "none":
        return 0.0, 0.0
    dc = float(s.get("day_count", 360))
    if model == "annual_rate":                    # v1 stores a fraction per year on notional
        scale = 360.0 / dc                        # v2 accrues calendar days / 360
        return float(s["long"]) * 100 * scale, float(s["short"]) * 100 * scale
    if quote_ccy != "USD":
        raise NeedsInput(f"swap in {quote_ccy} per day (only USD converted)")
    p = _need(price, "reference price")
    if model == "points_per_day":                 # points x point_size price units per unit per day
        per_day = float(s.get("point_size", 1.0)) / p
        return float(s["long"]) * per_day * 360 * 100, float(s["short"]) * per_day * 360 * 100
    if model == "currency_per_lot_day":           # quote currency per lot per day
        per_day = 1.0 / (contract_size * p)
        return float(s["long"]) * per_day * 360 * 100, float(s["short"]) * per_day * 360 * 100
    raise ValueError(f"unknown swap model {model}")


def convert_profile(prof: dict, price: float | None = None, notional: float | None = None,
                    atr_pct: float | None = None) -> tuple[SymbolCost, list[str]]:
    """One resolved v1 profile -> v2 SymbolCost (+ conversion notes)."""
    notes = []
    pip = prof.get("pip_size")
    sp = spread_bps(prof["spread"], price, pip)
    cm = commission_bps(prof.get("commission", {"model": "none"}), price, notional)
    slip = prof.get("slippage") or {}
    sl = amount_bps(slip.get("fixed", {"value": 0.0}), price, pip)
    frac = float(slip.get("atr_fraction", 0.0))
    if frac > 0:
        sl += frac * _need(atr_pct, "ATR% for atr_fraction slippage") * 1e4
        notes.append(f"slippage includes {frac:g} x ATR% {atr_pct:.4g}")
    swl, sws = swap_pct(prof.get("swap", {"model": "none"}), price, float(prof.get("contract_size", 1.0)),
                        prof.get("quote_ccy", "USD"))
    if prof["spread"]["mode"] == "hourly_profile":
        notes.append("hourly spread profile averaged over 24 h")
    if prof["spread"]["mode"] == "broker_scaled":
        notes.append("broker_scaled spread: broker reference spread, no hourly shape")
    return SymbolCost(sp, cm, sl, swl, sws), notes


def convert_all(symbols: dict[str, str], profiles: dict, groups: dict, assigned: dict,
                prices: dict | None = None, notional: float | None = None,
                atr_pct: dict | None = None) -> tuple[list[Converted], dict[str, str]]:
    """symbols: research symbol -> asset class. Returns (converted rows, symbol -> reason not converted)."""
    prices, atr_pct = prices or {}, atr_pct or {}
    out, skipped = [], {}
    for sym, cls in sorted(symbols.items()):
        try:
            prof = resolve(sym, cls, profiles, groups, assigned)
            cost, notes = convert_profile(prof, prices.get(sym), notional, atr_pct.get(sym))
        except (KeyError, NeedsInput) as exc:
            skipped[sym] = str(exc).strip("'\"")
            continue
        out.append(Converted(sym, cost, prof["name"], prof.get("status", ""), prof.get("broker_symbol"),
                             prices.get(sym), tuple(prof.get("to_verify") or ()), notes))
    return out, skipped


def write_csv(rows: list[Converted], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        for r in rows:
            c = r.cost
            w.writerow([r.symbol, f"{c.spread_bps:.6g}", f"{c.commission_bps:.6g}", f"{c.slippage_bps:.6g}",
                        f"{c.swap_long_pct:.6g}", f"{c.swap_short_pct:.6g}", r.profile, r.status,
                        r.broker_symbol or "", "" if r.ref_price is None else f"{r.ref_price:.6g}",
                        ";".join(r.to_verify), "; ".join(r.notes)])


def write_broker_map(rows: list[Converted], path: str | Path) -> dict[str, str]:
    """research -> broker symbol for `broker.mt5.MT5Broker(symbol_map=...)` (mapped symbols only)."""
    m = {r.symbol: r.broker_symbol for r in rows if r.broker_symbol}
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(("research_symbol", "broker_symbol"))
        w.writerows(sorted(m.items()))
    return m


def read_broker_map(path: str | Path) -> dict[str, str]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["research_symbol"]: r["broker_symbol"] for r in csv.DictReader(fh)}
