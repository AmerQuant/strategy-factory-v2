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


def taxonomy(cfg: LadderConfig) -> dict:
    """Edge-taxonomy labels (design 13.2) used to find empty cells and to budget correlation."""
    fam = FAMILY[cfg.method]
    return {"edge_source": "behavioural" if fam == "MR" else "behavioural/risk-premium",
            "signal_type": "price time-series", "horizon": "days" if fam == "MR" else "weeks",
            "asset_class": "equities", "direction": "buy" if cfg.direction == 1 else "sell", "family": fam}
