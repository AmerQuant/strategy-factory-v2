"""Per-symbol cost model (design 6.3): spread / commission / slippage in bps of traded notional per side, and
overnight financing (swap) in % per year of the position notional, charged per calendar day held
(day count 360, so the broker's triple-Friday rollover is covered by counting Sat and Sun). A negative rate is a
charge. CFD shares and ETFs at a CFD broker pay swap; cash equities do not (swap 0).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SymbolCost:
    spread_bps: float = 4.0       # full bid-ask spread; half is paid per side
    commission_bps: float = 1.0
    slippage_bps: float = 1.0
    swap_long_pct: float = 0.0     # % per year, negative = charge
    swap_short_pct: float = 0.0

    @property
    def per_side_bps(self) -> float:
        return self.spread_bps / 2 + self.commission_bps + self.slippage_bps


@dataclass(frozen=True)
class CostModel:
    default: SymbolCost = SymbolCost()
    overrides: dict = field(default_factory=dict)  # symbol -> SymbolCost
    stress: float = 1.0                            # 1.5 / 2.0 for stress tests

    @classmethod
    def flat(cls, per_side_bps: float) -> CostModel:
        return cls(SymbolCost(0.0, per_side_bps, 0.0))

    day_count: int = 360

    def per_side_bps(self, symbol: str) -> float:
        return self.overrides.get(symbol, self.default).per_side_bps * self.stress

    def swap_pct(self, symbol: str, direction: int) -> float:
        """Annual financing rate for a position; stress scales charges only (never credits)."""
        c = self.overrides.get(symbol, self.default)
        r = c.swap_long_pct if direction == 1 else c.swap_short_pct
        return r * self.stress if r < 0 else r

    @property
    def has_swap(self) -> bool:
        return any(c.swap_long_pct or c.swap_short_pct for c in (self.default, *self.overrides.values()))

    def fingerprint(self) -> str:
        payload = {"d": self.default.__dict__, "o": {k: v.__dict__ for k, v in sorted(self.overrides.items())},
                   "s": self.stress, "dc": self.day_count}
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]

    def stressed(self, factor: float) -> CostModel:
        return CostModel(self.default, self.overrides, self.stress * factor, self.day_count)

    @classmethod
    def moneta_share_cfd_proxy(cls) -> CostModel:
        """Moneta Markets MT5 share/ETF CFDs, provisional until the per-symbol profiles are imported.

        Swap from the v1 moneta.yaml ETF assumption (-6.88 % long / -3.5 % short per year, D-322),
        spread / commission / slippage from the v1 us_equity placeholder (2 bps full spread, ~1 bp commission,
        1 bp slippage). Replace with `load_cost_overrides` once the broker table is exported.
        """
        return cls(SymbolCost(2.0, 1.0, 1.0, -6.88, -3.5))


def load_cost_overrides(path, base: CostModel | None = None) -> CostModel:
    """Per-symbol costs from a CSV with columns
    symbol, spread_bps, commission_bps, slippage_bps, swap_long_pct, swap_short_pct (missing -> base default)."""
    import csv

    base = base or CostModel()
    d = base.default
    over = dict(base.overrides)
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            def g(k, dflt, row=row):
                v = row.get(k)
                return float(v) if v not in (None, "") else dflt
            over[row["symbol"]] = SymbolCost(g("spread_bps", d.spread_bps), g("commission_bps", d.commission_bps),
                                             g("slippage_bps", d.slippage_bps), g("swap_long_pct", d.swap_long_pct),
                                             g("swap_short_pct", d.swap_short_pct))
    return CostModel(d, over, base.stress, base.day_count)


COST_FIELDS = ("spread_bps", "commission_bps", "slippage_bps", "swap_long_pct", "swap_short_pct")


def load_cost_table(path) -> tuple[CostModel, set]:
    """Strict per-symbol costs (FX / index / metal CFDs): every row needs all five cost columns, and there is no
    usable default - the model's default is NaN, so a symbol without a row can never be priced silently (callers
    keep only the returned symbols). Returns (model, symbols with a cost row)."""
    import csv

    over = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            empty = [k for k in COST_FIELDS if row.get(k) in (None, "")]
            if empty:
                raise ValueError(f"{path} line {n} ({row.get('symbol')}): missing {empty}")
            over[row["symbol"]] = SymbolCost(*(float(row[k]) for k in COST_FIELDS))
    nan = float("nan")
    return CostModel(SymbolCost(nan, nan, nan, nan, nan), over), set(over)
