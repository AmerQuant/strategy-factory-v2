"""MetaTrader 5 adapter (official `MetaTrader5` Python package, Windows only) with the same interface as the
simulated broker: `positions()` -> {symbol: signed net volume} and `execute(net_orders, open_px, day)` -> fills.

- Only positions with this adapter's `magic` number are ours; anything else on the account is left alone and
  shows up as a reconciliation difference only if it is on a symbol we trade.
- Netting accounts: one market deal per net order. Hedging accounts (MT5 retail hedging, the usual CFD setup):
  a reducing order first closes our open positions on that symbol by ticket (oldest first) and only the rest
  opens a new position, so the account stays one net position per symbol, as the books assume.
- Volumes are rounded down to the symbol's `volume_step` and dropped below `volume_min` (reported, not sent).
- `dry_run=True` builds every request without sending it (first contact with a live account).
- `symbol_map` maps research symbols to broker symbols (e.g. "AAPL" -> "AAPL.US").
Not yet run against a live terminal from the development environment (no Windows / MT5 there): the tests use
a fake terminal with the documented API surface.
"""
from __future__ import annotations

from datetime import date

from sfactory.broker.orders import Fill, NetOrder


class MT5Error(RuntimeError):
    pass


class MT5Broker:
    def __init__(self, mt5=None, magic: int = 20260925, deviation: int = 20, symbol_map: dict | None = None,
                 dry_run: bool = False, comment: str = "sf2"):
        if mt5 is None:
            import MetaTrader5 as mt5
        self.mt5, self.magic, self.deviation, self.comment = mt5, magic, deviation, comment
        self.map = dict(symbol_map or {})
        self.inv = {v: k for k, v in self.map.items()}
        self.dry_run = dry_run
        self.requests: list[dict] = []
        self.skipped: list[dict] = []

    # --- session ----------------------------------------------------------------------------------------
    def connect(self, login: int | None = None, password: str | None = None, server: str | None = None,
                path: str | None = None) -> dict:
        kw = {k: v for k, v in (("login", login), ("password", password), ("server", server), ("path", path))
              if v is not None}
        if not self.mt5.initialize(**kw):
            raise MT5Error(f"initialize failed: {self.mt5.last_error()}")
        acc = self.mt5.account_info()
        return {"login": acc.login, "server": acc.server, "hedging": self._hedging(), "currency": acc.currency}

    def _hedging(self) -> bool:
        return self.mt5.account_info().margin_mode == self.mt5.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING

    # --- state --------------------------------------------------------------------------------------------
    def _ours(self, broker_symbol: str | None = None) -> list:
        ps = self.mt5.positions_get(symbol=broker_symbol) if broker_symbol else self.mt5.positions_get()
        return [p for p in (ps or ()) if p.magic == self.magic]

    def positions(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self._ours():
            s = self.inv.get(p.symbol, p.symbol)
            sign = 1.0 if p.type == self.mt5.POSITION_TYPE_BUY else -1.0
            out[s] = out.get(s, 0.0) + sign * p.volume
        return {s: q for s, q in out.items() if abs(q) > 1e-12}

    # --- orders -------------------------------------------------------------------------------------------
    def _request(self, bsym: str, qty: float, position: int | None = None) -> dict:
        tick = self.mt5.symbol_info_tick(bsym)
        buy = qty > 0
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": bsym, "volume": abs(qty),
               "type": self.mt5.ORDER_TYPE_BUY if buy else self.mt5.ORDER_TYPE_SELL,
               "price": tick.ask if buy else tick.bid, "deviation": self.deviation, "magic": self.magic,
               "comment": self.comment, "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self.mt5.ORDER_FILLING_IOC}
        if position is not None:
            req["position"] = position
        return req

    def _send(self, req: dict) -> tuple[float, float, str]:
        self.requests.append(req)
        if self.dry_run:
            return req["volume"], req["price"], "dry-run"
        res = self.mt5.order_send(req)
        if res is None or res.retcode != self.mt5.TRADE_RETCODE_DONE:
            code = None if res is None else res.retcode
            raise MT5Error(f"order_send failed for {req['symbol']}: retcode {code}, {self.mt5.last_error()}")
        return res.volume, res.price, str(res.order)

    def _round(self, bsym: str, qty: float) -> float:
        info = self.mt5.symbol_info(bsym)
        if info is None:
            raise MT5Error(f"unknown symbol {bsym}")
        step = info.volume_step
        vol = int(abs(qty) / step + 1e-9) * step
        if vol < info.volume_min:
            self.skipped.append({"symbol": bsym, "qty": qty, "reason": f"below volume_min {info.volume_min}"})
            return 0.0
        return round(vol, 8) * (1 if qty > 0 else -1)

    def execute(self, nets: list[NetOrder], open_px: dict[str, float], day: date) -> dict[str, Fill]:
        out = {}
        hedging = self._hedging()
        for n in nets:
            if abs(n.qty) < 1e-12:
                continue
            bsym = self.map.get(n.symbol, n.symbol)
            qty = self._round(bsym, n.qty)
            if qty == 0:
                continue
            legs = []
            if hedging:
                rest = qty
                for p in sorted(self._ours(bsym), key=lambda p: p.time):
                    sign = 1.0 if p.type == self.mt5.POSITION_TYPE_BUY else -1.0
                    if sign * rest >= 0 or abs(rest) < 1e-12:
                        break                                   # same side: nothing to close
                    close = min(p.volume, abs(rest))
                    legs.append((-sign * close, p.ticket))
                    rest += sign * close
                if abs(rest) > 1e-12:
                    legs.append((rest, None))
            else:
                legs = [(qty, None)]
            vol_px, filled, refs = 0.0, 0.0, []
            for q, ticket in legs:
                v, px, ref = self._send(self._request(bsym, q, ticket))
                filled += v * (1 if q > 0 else -1)
                vol_px += v * px
                refs.append(ref)
            if filled:
                out[n.symbol] = Fill(n.symbol, filled, vol_px / abs(filled), 0.0, day, ",".join(refs))
        return out
