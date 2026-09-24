"""Per-symbol cost model (design 6.3). All figures in basis points of traded notional, per side."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SymbolCost:
    spread_bps: float = 4.0       # full bid-ask spread; half is paid per side
    commission_bps: float = 1.0
    slippage_bps: float = 1.0

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

    def per_side_bps(self, symbol: str) -> float:
        return self.overrides.get(symbol, self.default).per_side_bps * self.stress

    def fingerprint(self) -> str:
        payload = {"d": self.default.__dict__, "o": {k: v.__dict__ for k, v in sorted(self.overrides.items())},
                   "s": self.stress}
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]

    def stressed(self, factor: float) -> CostModel:
        return CostModel(self.default, self.overrides, self.stress * factor)
