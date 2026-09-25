"""Pre-registered row catalogue (design 7.3): every method x direction on the equity universe.

Adding a row after results have been seen is a new trial (design ch. 8); the catalogue is versioned for that reason.
The diverse families (design 13.2: volatility, cross-sectional momentum, calendar, events) are a separate,
separately versioned catalogue, so adding them never changes the meaning of the original 18 rows.
"""
from __future__ import annotations

from sfactory.policy.ladder import LadderConfig
from sfactory.signals.methods import FAMILY

CATALOG_VERSION = "2026-10-v1"
MR_METHODS = ("rsi", "ibs", "consec", "lowest_close", "donchian_low")
TF_METHODS = ("ma_cross", "donchian_break", "supertrend", "ichimoku")
DIVERSE_CATALOG_VERSION = "2026-10-families-v1"
CFD_CATALOG_VERSION = "2026-10-cfd-v1"      # FX / index / metal CFDs (Moneta MT5): FX, IX, MT rows
CFD_LABEL = {"fx": "fx", "index_cfd": "indices", "metal": "metals"}   # v1 store class -> row asset class
DIVERSE_METHODS = ("vol_spike", "squeeze", "xs_mom", "tom", "post_exdiv", "index_add")
# (method, directions): sells only where the mirror is meaningful
DIVERSE_ROWS = (("vol_spike", (1, -1)), ("squeeze", (1, -1)), ("xs_mom", (1, -1)), ("tom", (1,)),
                ("post_exdiv", (1,)), ("index_add", (1,)))
_TAXONOMY = {
    "MR": ("behavioural", "price time-series", "days"),
    "TF": ("behavioural/risk-premium", "price time-series", "weeks"),
    "VOL": ("behavioural/liquidity", "volatility state", "days"),
    "XS": ("risk-premium/behavioural", "cross-sectional", "months"),
    "CAL": ("structural/flows", "calendar", "days"),
    "EV": ("structural/event", "event", "days"),
}


def equity_rows(base: LadderConfig | None = None) -> list[LadderConfig]:
    from dataclasses import replace
    base = base or LadderConfig()
    return [replace(base, method=m, direction=d) for m in MR_METHODS + TF_METHODS for d in (1, -1)]


def taxonomy(cfg) -> dict:
    """Edge-taxonomy labels (design 13.2) used to find empty cells and to budget correlation."""
    fam = cfg.family if cfg.method == "ensemble" else FAMILY[cfg.method]
    src, sig, hor = _TAXONOMY[fam]
    return {"edge_source": src, "signal_type": sig, "horizon": hor,
            "asset_class": cfg.asset_class, "direction": "buy" if cfg.direction == 1 else "sell", "family": fam,
            "ensemble": cfg.method == "ensemble"}


def fx_rows(base: LadderConfig | None = None, top_n: int = 4) -> list[LadderConfig]:
    """Symbol-based rows for FX-like markets: no volume or price filters (volume is not meaningful for FX),
    no dividends, and S7 selection of the top-N symbols by IS t-stat at every DP."""
    from dataclasses import replace
    base = base or LadderConfig()
    base = replace(base, min_price=0.0, min_dollar_vol=0.0, symbol_select=top_n, asset_class="fx")
    return [replace(base, method=m, direction=d) for m in MR_METHODS + TF_METHODS for d in (1, -1)]


def cfd_rows(asset_class: str, top_n: int, base: LadderConfig | None = None) -> list[LadderConfig]:
    """The CFD catalogue (version CFD_CATALOG_VERSION): every MR / TF method x direction for one v1 store class
    (`fx`, `index_cfd`, `metal`), rows labelled FX / IX / MT. No price or dollar-volume filters (CFD volume is not
    meaningful), static membership. FX and index rows select the top-N symbols by IS t-stat at every DP (S7,
    `top_n` required); metal rows trade the class pooled (two symbols: selection is meaningless, owner's choice)."""
    from dataclasses import replace
    if asset_class not in CFD_LABEL:
        raise ValueError(f"asset class {asset_class} not in {sorted(CFD_LABEL)}")
    if asset_class == "metal" and top_n:
        raise ValueError("metal rows are pooled: top_n must be 0")
    if asset_class != "metal" and top_n <= 0:
        raise ValueError(f"{asset_class} rows need top_n > 0 (S7 symbol selection)")
    base = base or LadderConfig()
    base = replace(base, min_price=0.0, min_dollar_vol=0.0, symbol_select=top_n, asset_class=CFD_LABEL[asset_class],
                   universe_mode="membership")
    return [replace(base, method=m, direction=d) for m in MR_METHODS + TF_METHODS for d in (1, -1)]


def ensemble_rows(rows: list[LadderConfig]) -> list:
    """One tradable ensemble per family x direction present in `rows`."""
    from sfactory.policy.ensemble import family_ensemble_row
    keys = sorted({(r.family, r.direction) for r in rows})
    return [family_ensemble_row(rows, f, d, asset_class=rows[0].asset_class) for f, d in keys]


def diverse_rows(base: LadderConfig | None = None, dividends: bool = False, index_events: bool = False,
                 min_positions: int = 10) -> list[LadderConfig]:
    """Rows of the diverse families. Event rows need their data (dividends / index membership events) and are
    left out otherwise, so empty rows never add trials. XS rows need a capacity limit (the cross-section is
    formed by the daily ranker), so max_positions is raised to `min_positions` when the base has none.
    Index-addition rows use the top-liquidity universe: an added symbol is not yet an index member at the DP."""
    from dataclasses import replace
    base = base or LadderConfig()
    out = []
    for m, dirs in DIVERSE_ROWS:
        if (m == "post_exdiv" and not dividends) or (m == "index_add" and not index_events):
            continue
        b = base
        if m == "xs_mom" and b.max_positions <= 0:
            b = replace(b, max_positions=min_positions)
        if m == "index_add":
            b = replace(b, universe_mode="top_liquidity")
        out += [replace(b, method=m, direction=d) for d in dirs]
    return out
