"""Symbol specifications from a MetaTrader 5 terminal (Moneta, ECN account) -> one CSV row per symbol.

Read from `symbol_info` (official `MetaTrader5` package, Windows): contract size, point / digits, tick size and tick
value (profit / loss variants), volume limits, profit / base / margin currency, calculation mode, swap long / short,
swap mode and the triple-swap weekday. Enumerations are written as the NAMES of the package's own constants
(`SYMBOL_SWAP_MODE_POINTS` -> "points"), never as numbers, so nothing depends on guessing their values.

Spread: the broker's historical spread from the `spread` field of its own bars (`copy_rates_from_pos`, in points),
turned into bps of each bar's close (`spread * point / close * 1e4`); the export writes the mean and the median, the
number of bars and the median close of the same bars (the reference price for per-lot and currency conversions in
`costs.mt5_costs`). Which statistic MT5 stores per bar in `spread` (the bar's minimum, or its last) is not verified
here - check on the terminal before relying on it. The current `spread` of `symbol_info` is written for reference
only (it depends on the moment of the export).

Not in `symbol_info` and therefore not exported: the account's commission per lot (ECN) and slippage - they come
from the manual file read by `costs.mt5_costs`; trading sessions - the Python package has no session function, so
they come from a hand-written sessions file (`data.sessions`).
Tested against a fake terminal only (no Windows / MT5 in the development environment).
"""
from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

SPEC_COLUMNS = (
    "research_symbol", "broker_symbol", "description", "path", "currency_base", "currency_profit", "currency_margin",
    "deposit_currency", "digits", "point", "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "trade_tick_value_profit", "trade_tick_value_loss", "volume_min", "volume_step", "volume_max", "calc_mode",
    "swap_mode", "swap_long", "swap_short", "swap_rollover3days", "spread_now_points", "spread_float",
    "spread_timeframe", "spread_bars", "spread_zero_bars", "spread_points_mean", "spread_points_median",
    "spread_bps_mean", "spread_bps_median", "ref_close_median", "history_from", "history_to", "exported_at",
)
NUMERIC = {"digits", "point", "trade_contract_size", "trade_tick_size", "trade_tick_value", "trade_tick_value_profit",
           "trade_tick_value_loss", "volume_min", "volume_step", "volume_max", "swap_long", "swap_short",
           "swap_rollover3days", "spread_now_points", "spread_bars", "spread_zero_bars", "spread_points_mean",
           "spread_points_median", "spread_bps_mean", "spread_bps_median", "ref_close_median"}


def enum_name(mt5, prefix: str, value) -> str:
    """Name of the package constant `prefix + NAME` whose value is `value` ("points"); "unknown:<v>" if none."""
    names = sorted(k for k in dir(mt5) if k.startswith(prefix) and getattr(mt5, k) == value)
    return names[0][len(prefix):].lower() if names else f"unknown:{value}"


def spread_stats(mt5, broker_symbol: str, point: float, timeframe: str = "H1", bars: int = 2000) -> dict:
    """Mean / median spread of the broker's last `bars` bars (points and bps of the bar close)."""
    tf = getattr(mt5, f"TIMEFRAME_{timeframe}")
    rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        return {"spread_bars": 0}
    sp = np.asarray(rates["spread"], float)
    close = np.asarray(rates["close"], float)
    ok = close > 0
    sp, close, t = sp[ok], close[ok], np.asarray(rates["time"])[ok]
    if len(sp) == 0:
        return {"spread_bars": 0}
    bps = sp * point / close * 1e4
    return {"spread_timeframe": timeframe, "spread_bars": len(sp), "spread_zero_bars": int((sp <= 0).sum()),
            "spread_points_mean": float(sp.mean()), "spread_points_median": float(np.median(sp)),
            "spread_bps_mean": float(bps.mean()), "spread_bps_median": float(np.median(bps)),
            "ref_close_median": float(np.median(close)),
            "history_from": datetime.fromtimestamp(int(t.min()), UTC).isoformat(timespec="minutes"),
            "history_to": datetime.fromtimestamp(int(t.max()), UTC).isoformat(timespec="minutes")}


def export_specs(mt5, symbols: dict[str, str], timeframe: str = "H1", bars: int = 2000,
                 now: datetime | None = None) -> tuple[list[dict], dict[str, str]]:
    """symbols: research symbol -> broker symbol. Returns (rows, research symbol -> reason not exported)."""
    now = now or datetime.now(UTC)
    acc = mt5.account_info()
    deposit = getattr(acc, "currency", "") if acc is not None else ""
    rows, skipped = [], {}
    for rs, bs in sorted(symbols.items()):
        if hasattr(mt5, "symbol_select") and not mt5.symbol_select(bs, True):
            skipped[rs] = f"symbol_select({bs}) failed: {mt5.last_error()}"
            continue
        info = mt5.symbol_info(bs)
        if info is None:
            skipped[rs] = f"broker symbol {bs} not found"
            continue
        row = {"research_symbol": rs, "broker_symbol": bs, "description": getattr(info, "description", ""),
               "path": getattr(info, "path", ""), "currency_base": info.currency_base,
               "currency_profit": info.currency_profit, "currency_margin": info.currency_margin,
               "deposit_currency": deposit, "digits": info.digits, "point": info.point,
               "trade_contract_size": info.trade_contract_size, "trade_tick_size": info.trade_tick_size,
               "trade_tick_value": info.trade_tick_value,
               "trade_tick_value_profit": getattr(info, "trade_tick_value_profit", None),
               "trade_tick_value_loss": getattr(info, "trade_tick_value_loss", None),
               "volume_min": info.volume_min, "volume_step": info.volume_step, "volume_max": info.volume_max,
               "calc_mode": enum_name(mt5, "SYMBOL_CALC_MODE_", info.trade_calc_mode),
               "swap_mode": enum_name(mt5, "SYMBOL_SWAP_MODE_", info.swap_mode),
               "swap_long": info.swap_long, "swap_short": info.swap_short,
               "swap_rollover3days": getattr(info, "swap_rollover3days", None),
               "spread_now_points": info.spread, "spread_float": bool(getattr(info, "spread_float", False)),
               "exported_at": now.isoformat(timespec="seconds")}
        row.update(spread_stats(mt5, bs, float(info.point), timeframe, bars))
        rows.append(row)
    return rows, skipped


def write_specs_csv(rows: list[dict], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, SPEC_COLUMNS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in SPEC_COLUMNS})


def read_specs_csv(path: str | Path) -> dict[str, dict]:
    """research symbol -> spec row (numbers parsed; empty cells stay None)."""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["research_symbol"]] = {k: (None if v in ("", None) else float(v) if k in NUMERIC else v)
                                         for k, v in r.items()}
    return out
