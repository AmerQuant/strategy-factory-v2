"""Pre-registered row catalogue (design 7.3): every method x direction on the equity universe.

Adding a row after results have been seen is a new trial (design ch. 8); the catalogue is versioned for that reason.
"""
from __future__ import annotations

from sfactory.policy.ladder import LadderConfig
from sfactory.signals.methods import FAMILY

CATALOG_VERSION = "2026-10-v1"
MR_METHODS = ("rsi", "ibs", "consec", "lowest_close", "donchian_low")
TF_METHODS = ("ma_cross", "donchian_break", "supertrend", "ichimoku")


def equity_rows(base: LadderConfig | None = None) -> list[LadderConfig]:
    from dataclasses import replace
    base = base or LadderConfig()
    return [replace(base, method=m, direction=d) for m in MR_METHODS + TF_METHODS for d in (1, -1)]


def taxonomy(cfg) -> dict:
    """Edge-taxonomy labels (design 13.2) used to find empty cells and to budget correlation."""
    fam = cfg.family if cfg.method == "ensemble" else FAMILY[cfg.method]
    return {"edge_source": "behavioural" if fam == "MR" else "behavioural/risk-premium",
            "signal_type": "price time-series", "horizon": "days" if fam == "MR" else "weeks",
            "asset_class": cfg.asset_class, "direction": "buy" if cfg.direction == 1 else "sell", "family": fam,
            "ensemble": cfg.method == "ensemble"}


def fx_rows(base: LadderConfig | None = None, top_n: int = 4) -> list[LadderConfig]:
    """Symbol-based rows for FX-like markets: no volume or price filters (volume is not meaningful for FX),
    no dividends, and S7 selection of the top-N symbols by IS t-stat at every DP."""
    from dataclasses import replace
    base = base or LadderConfig()
    base = replace(base, min_price=0.0, min_dollar_vol=0.0, symbol_select=top_n, asset_class="fx")
    return [replace(base, method=m, direction=d) for m in MR_METHODS + TF_METHODS for d in (1, -1)]


def ensemble_rows(rows: list[LadderConfig]) -> list:
    """One tradable ensemble per family x direction present in `rows`."""
    from sfactory.policy.ensemble import family_ensemble_row
    keys = sorted({(r.family, r.direction) for r in rows})
    return [family_ensemble_row(rows, f, d, asset_class=rows[0].asset_class) for f, d in keys]
