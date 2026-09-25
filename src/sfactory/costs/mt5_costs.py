"""MT5 symbol specifications (`broker/mt5_specs.py` CSV) + a manual file -> the v2 per-symbol cost CSV.

v2 prices costs in bps of traded notional per side plus swap in % per year on a 360-day accrual (`costs.model`).
Per symbol:

| input | v2 |
|---|---|
| spread: `spread_bps_mean` or `spread_bps_median` of the export (`spread_stat`, chosen explicitly) | `spread_bps` (full spread) |
| manual `commission_per_lot_side` in `commission_currency` (USD only) / USD notional of one lot | `commission_bps` |
| manual `slippage_bps` | `slippage_bps` |
| swap `points`: points x point / price x 360 x 100 | `swap_long_pct`, `swap_short_pct` |
| swap `interest_current` / `interest_open`: already % per year (open: on the open price, flagged) | same |
| swap `currency_symbol` / `currency_margin` / `currency_deposit`: amount per lot per day in that currency (USD only) / USD notional x 360 x 100 | same |

USD notional of one lot: `contract_size x ref_close_median` when the profit currency is USD; `contract_size` when the
base currency is USD and the calculation mode is forex; otherwise unknown. Nothing is defaulted silently: a symbol
that needs a value that is missing (no spread history, no manual row, empty slippage, non-USD amounts, unknown USD
notional, `reopen_*` or unknown swap modes) is left out of the CSV and reported with the reason.
The swap is accrued per calendar day / 360 as everywhere in v2 (owner's choice); the broker's triple-swap weekday
is carried in the notes, not modelled.
"""
from __future__ import annotations

import csv
from pathlib import Path

from sfactory.costs.convert import Converted, NeedsInput
from sfactory.costs.model import SymbolCost

MANUAL_COLUMNS = ("symbol", "commission_per_lot_side", "commission_currency", "slippage_bps")
FOREX_MODES = {"forex", "forex_no_leverage"}
CURRENCY_SWAPS = {"currency_symbol": "currency_base", "currency_margin": "currency_margin",
                  "currency_deposit": "deposit_currency"}


def read_manual(path: str | Path) -> dict[str, dict]:
    """symbol -> {commission_per_lot_side, commission_currency, slippage_bps}; empty cells stay None."""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        missing = [c for c in MANUAL_COLUMNS if c not in (rd.fieldnames or ())]
        if missing:
            raise ValueError(f"manual cost file needs the columns {MANUAL_COLUMNS}; missing {missing}")
        for r in rd:
            def num(k, r=r):
                v = (r.get(k) or "").strip()
                return float(v) if v else None
            out[r["symbol"].strip()] = {"commission_per_lot_side": num("commission_per_lot_side"),
                                        "commission_currency": (r.get("commission_currency") or "").strip().upper(),
                                        "slippage_bps": num("slippage_bps")}
    return out


def _need(v, what: str):
    if v is None:
        raise NeedsInput(what)
    return v


def usd_notional_per_lot(spec: dict) -> float | None:
    size = spec.get("trade_contract_size")
    if size is None:
        return None
    if spec.get("currency_profit") == "USD" and spec.get("ref_close_median"):
        return size * spec["ref_close_median"]
    if spec.get("currency_base") == "USD" and spec.get("calc_mode") in FOREX_MODES:
        return size
    return None


def convert_symbol(spec: dict, manual: dict | None, spread_stat: str) -> tuple[SymbolCost, list, list]:
    """(cost, notes, to_verify) for one symbol, or NeedsInput with the reason."""
    if spread_stat not in ("mean", "median"):
        raise ValueError("spread_stat must be 'mean' or 'median'")
    notes, verify = [], []
    if not spec.get("spread_bars"):
        raise NeedsInput("no spread history in the MT5 export")
    spread = _need(spec.get(f"spread_bps_{spread_stat}"), f"spread_bps_{spread_stat}")
    notes.append(f"spread {spread_stat} of {int(spec['spread_bars'])} {spec.get('spread_timeframe') or ''} bars")
    if spec.get("spread_zero_bars"):
        verify.append("zero_spread_bars")
    if manual is None:
        raise NeedsInput("no row in the manual cost file")
    slip = _need(manual.get("slippage_bps"), "slippage_bps in the manual file")
    comm = _need(manual.get("commission_per_lot_side"), "commission_per_lot_side in the manual file")
    notional = usd_notional_per_lot(spec)
    comm_bps = 0.0
    if comm != 0:
        if manual.get("commission_currency") != "USD":
            raise NeedsInput(f"commission in {manual.get('commission_currency') or '?'} (only USD converted)")
        comm_bps = comm / _need(notional, "USD notional of one lot") * 1e4
    mode = spec.get("swap_mode") or ""
    sl, ss = spec.get("swap_long") or 0.0, spec.get("swap_short") or 0.0
    if mode == "disabled":
        swl = sws = 0.0
    elif mode == "points":
        per = _need(spec.get("point"), "point") / _need(spec.get("ref_close_median"), "reference price") * 360 * 100
        swl, sws = sl * per, ss * per
    elif mode in ("interest_current", "interest_open"):
        swl, sws = sl, ss
        if mode == "interest_open":
            verify.append("swap_interest_open_approx")
    elif mode in CURRENCY_SWAPS:
        ccy = spec.get(CURRENCY_SWAPS[mode]) or "?"
        if ccy != "USD":
            raise NeedsInput(f"swap in {ccy} per lot (only USD converted)")
        per = 360 * 100 / _need(notional, "USD notional of one lot")
        swl, sws = sl * per, ss * per
    else:
        raise NeedsInput(f"swap mode {mode or '?'} not converted")
    notes.append(f"swap mode {mode}; triple swap weekday {spec.get('swap_rollover3days')} (not modelled)")
    return SymbolCost(spread, comm_bps, slip, swl, sws), notes, verify


def convert_all_mt5(specs: dict[str, dict], manual: dict[str, dict], spread_stat: str,
                    symbols: list[str] | None = None) -> tuple[list[Converted], dict[str, str]]:
    out, skipped = [], {}
    for sym in sorted(symbols if symbols is not None else specs):
        spec = specs.get(sym)
        if spec is None:
            skipped[sym] = "not in the MT5 export"
            continue
        try:
            cost, notes, verify = convert_symbol(spec, manual.get(sym), spread_stat)
        except NeedsInput as exc:
            skipped[sym] = str(exc)
            continue
        out.append(Converted(sym, cost, "mt5_spec", "mt5_export", spec.get("broker_symbol"),
                             spec.get("ref_close_median"), tuple(verify), notes))
    return out, skipped
