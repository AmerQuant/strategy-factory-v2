"""Order / fill contract between the policy and any broker (paper, simulated or MT5), and netting across rows.

Rows keep their own virtual positions (the research unit); the broker holds one net position per symbol.
Every row order is one `Order`; `net_orders` turns the day's row orders into one `NetOrder` per symbol and
`allocate` hands each broker fill back to the row orders it came from, so a row's pnl never depends on
another row. Orders that cancel inside the book (one row buys what another sells) are crossed internally at
the same open price the broker would use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Order:
    order_id: str
    row: str
    symbol: str
    qty: float                  # signed: > 0 buy, < 0 sell
    intent: str                 # open | close
    signal_date: date           # the signal bar of the position this order opens or closes
    ref_price: float            # close of the signal bar (reference for slippage / shortfall)


@dataclass(frozen=True)
class NetOrder:
    symbol: str
    qty: float                  # signed net quantity sent to the broker (0 = fully crossed internally)
    allocations: tuple          # ((order_id, qty), ...)


@dataclass(frozen=True)
class Fill:
    symbol: str
    qty: float                  # signed
    price: float
    commission: float = 0.0
    fill_date: date | None = None
    broker_ref: str = ""


@dataclass
class RowFill:
    order: Order
    price: float
    commission: float
    fill_date: date | None
    crossed: bool = False       # filled inside the book, no broker volume


@dataclass
class VirtualPosition:
    row: str
    symbol: str
    qty: float                  # signed
    signal_date: date
    entry_date: date | None
    entry_px: float
    commission: float = 0.0


@dataclass
class RowBook:
    """Virtual positions and closed trades of one row (same fields as the research trade frame)."""
    row: str
    positions: dict = field(default_factory=dict)      # symbol -> VirtualPosition
    closed: list = field(default_factory=list)

    def apply(self, rf: RowFill) -> None:
        o = rf.order
        if o.intent == "open":
            self.positions[o.symbol] = VirtualPosition(o.row, o.symbol, o.qty, o.signal_date, rf.fill_date,
                                                       rf.price, rf.commission)
            return
        p = self.positions.pop(o.symbol)
        gross = p.qty * (rf.price - p.entry_px)
        self.closed.append({"row": self.row, "symbol": o.symbol, "signal_date": p.signal_date,
                            "entry_date": p.entry_date, "exit_date": rf.fill_date, "entry_px": p.entry_px,
                            "exit_px": rf.price, "shares": abs(p.qty), "gross_pnl": gross,
                            "cost": p.commission + rf.commission,
                            "net_pnl": gross - p.commission - rf.commission})


def net_orders(orders: list[Order]) -> list[NetOrder]:
    by: dict[str, list[Order]] = {}
    for o in orders:
        by.setdefault(o.symbol, []).append(o)
    return [NetOrder(s, sum(o.qty for o in os_), tuple((o.order_id, o.qty) for o in os_))
            for s, os_ in sorted(by.items())]


def allocate(nets: list[NetOrder], fills: dict[str, Fill], orders: list[Order], open_px: dict[str, float],
             fill_date: date | None) -> list[RowFill]:
    """Broker fills -> row fills. The net traded quantity gets the broker's price and commission (commission
    split pro rata over the orders on the traded side); the crossed remainder gets the open price, no cost."""
    ids = {o.order_id: o for o in orders}
    out = []
    for n in nets:
        f = fills.get(n.symbol)
        px_cross = f.price if f is not None else open_px[n.symbol]
        side = 1 if n.qty > 0 else -1 if n.qty < 0 else 0
        same = [(oid, q) for oid, q in n.allocations if side != 0 and q * side > 0]
        vol_same = sum(abs(q) for _, q in same)
        traded = abs(n.qty)
        for oid, q in n.allocations:
            o = ids[oid]
            if f is not None and (oid, q) in same:
                share = abs(q) / vol_same
                broker_part = traded * share                  # part of this order that went to the broker
                crossed_part = abs(q) - broker_part
                price = (broker_part * f.price + crossed_part * px_cross) / abs(q)
                comm = f.commission * share
                out.append(RowFill(o, price, comm, fill_date, crossed=broker_part == 0))
            else:
                out.append(RowFill(o, px_cross, 0.0, fill_date, crossed=True))
    return out
