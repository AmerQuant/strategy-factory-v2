"""Simulated broker for paper mode and tests: market-on-open fills with the research cost model, one net
position per symbol (MT5 netting semantics), deterministic.

Costs are charged exactly as the research engine does (per-side bps of traded notional, fill at the open), so a
paper run is comparable trade for trade with the backtest; a separate `slippage_bps` lets paper mode add an
execution haircut on top when wanted.
"""
from __future__ import annotations

from datetime import date

from sfactory.broker.orders import Fill, NetOrder
from sfactory.costs.model import CostModel


class SimulatedBroker:
    def __init__(self, cost_model: CostModel | None = None, slippage_bps: float = 0.0):
        self.cost_model = cost_model or CostModel.flat(0.0)
        self.slippage_bps = slippage_bps
        self.net: dict[str, float] = {}
        self.fills: list[Fill] = []
        self._n = 0

    def positions(self) -> dict[str, float]:
        return {s: q for s, q in self.net.items() if abs(q) > 1e-9}

    def execute(self, nets: list[NetOrder], open_px: dict[str, float], day: date) -> dict[str, Fill]:
        out = {}
        for n in nets:
            if abs(n.qty) < 1e-12:
                continue
            side = 1 if n.qty > 0 else -1
            px = open_px[n.symbol] * (1 + side * self.slippage_bps / 1e4)
            comm = abs(n.qty) * px * self.cost_model.per_side_bps(n.symbol) / 1e4
            self._n += 1
            f = Fill(n.symbol, n.qty, px, comm, day, f"sim-{self._n}")
            self.net[n.symbol] = self.net.get(n.symbol, 0.0) + n.qty
            self.fills.append(f)
            out[n.symbol] = f
        return out
