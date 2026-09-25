from datetime import date
from types import SimpleNamespace as NS

import pytest

from sfactory.broker.mt5 import MT5Broker, MT5Error
from sfactory.broker.orders import NetOrder


class FakeMT5:
    """The documented MetaTrader5 API surface the adapter uses, with an in-memory account."""
    TRADE_ACTION_DEAL, ORDER_TYPE_BUY, ORDER_TYPE_SELL = 1, 0, 1
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TIME_GTC, ORDER_FILLING_IOC, TRADE_RETCODE_DONE = 0, 1, 10009
    ACCOUNT_MARGIN_MODE_RETAIL_NETTING, ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 0, 2

    def __init__(self, hedging=True, fail=False):
        self.mode = 2 if hedging else 0
        self.pos, self.sent, self.fail, self._t, self.deals = [], [], fail, 0, []

    def history_deals_get(self, date_from=None, date_to=None, ticket=None):
        if ticket is not None:
            return tuple(d for d in self.deals if d.ticket == ticket)
        return tuple(self.deals)

    def initialize(self, **kw):
        return True

    def last_error(self):
        return (1, "ok")

    def account_info(self):
        return NS(login=1, server="Moneta-Demo", currency="USD", margin_mode=self.mode)

    def symbol_info(self, s):
        return NS(volume_step=0.01, volume_min=0.01) if s != "BAD" else None

    def symbol_info_tick(self, s):
        return NS(ask=101.0, bid=100.0)

    def positions_get(self, symbol=None):
        return tuple(p for p in self.pos if symbol is None or p.symbol == symbol)

    def order_send(self, req):
        self.sent.append(req)
        if self.fail:
            return NS(retcode=10013, volume=0, price=0, order=0)
        self._t += 1
        sign = 1 if req["type"] == self.ORDER_TYPE_BUY else -1
        self.deals.append(NS(ticket=self._t, symbol=req["symbol"], magic=req["magic"], commission=-0.5 * req["volume"],
                             fee=-0.1, swap=-0.25 if "position" in req else 0.0))
        if "position" in req:
            p = next(p for p in self.pos if p.ticket == req["position"])
            p.volume = round(p.volume - req["volume"], 8)
            if p.volume <= 0:
                self.pos.remove(p)
        elif self.mode == 0 and (cur := [p for p in self.pos if p.symbol == req["symbol"]]):
            p = cur[0]
            net = (1 if p.type == 0 else -1) * p.volume + sign * req["volume"]
            self.pos.remove(p)
            if abs(net) > 1e-9:
                self.pos.append(NS(ticket=self._t, symbol=req["symbol"], volume=abs(net), type=0 if net > 0 else 1,
                                   magic=req["magic"], time=self._t))
        else:
            self.pos.append(NS(ticket=self._t, symbol=req["symbol"], volume=req["volume"],
                               type=0 if sign > 0 else 1, magic=req["magic"], time=self._t))
        return NS(retcode=self.TRADE_RETCODE_DONE, volume=req["volume"], price=req["price"], order=self._t,
                  deal=self._t)


D = date(2026, 9, 25)


@pytest.mark.parametrize("hedging", [True, False])
def test_net_orders_keep_one_net_position_per_symbol(hedging):
    fake = FakeMT5(hedging=hedging)
    br = MT5Broker(fake, symbol_map={"AAPL": "AAPL.US"})
    assert br.connect()["hedging"] is hedging
    f = br.execute([NetOrder("AAPL", 10.0, ())], {}, D)["AAPL"]
    assert f.qty == 10.0 and f.price == 101.0 and br.positions() == {"AAPL": 10.0}
    br.execute([NetOrder("AAPL", -14.0, ())], {}, D)
    assert br.positions() == {"AAPL": pytest.approx(-4.0)}
    assert len(fake.positions_get()) == 1                        # hedging too: long closed by ticket first
    if hedging:
        assert fake.sent[1]["position"] == 1 and fake.sent[1]["volume"] == 10.0   # ticket of the long


def test_foreign_positions_rounding_dry_run_and_errors():
    fake = FakeMT5()
    fake.pos.append(NS(ticket=99, symbol="MSFT", volume=5.0, type=0, magic=1, time=0))   # manual trade
    br = MT5Broker(fake)
    assert br.positions() == {}
    br.execute([NetOrder("X", 0.004, ()), NetOrder("Y", 1.2345, ())], {}, D)
    assert br.skipped[0]["symbol"] == "X" and fake.sent[-1]["volume"] == pytest.approx(1.23)
    dry = MT5Broker(FakeMT5(), dry_run=True)
    dry.execute([NetOrder("Z", 3.0, ())], {}, D)
    assert dry.requests and not dry.mt5.sent
    with pytest.raises(MT5Error):
        MT5Broker(FakeMT5(fail=True)).execute([NetOrder("Z", 1.0, ())], {}, D)
    with pytest.raises(MT5Error):
        MT5Broker(FakeMT5()).execute([NetOrder("BAD", 1.0, ())], {}, D)


def test_commissions_and_swap_from_the_deal_history():
    fake = FakeMT5(hedging=True)
    br = MT5Broker(fake, symbol_map={"AAPL": "AAPL.US"})
    f = br.execute([NetOrder("AAPL", 10.0, ())], {}, D)["AAPL"]
    assert f.commission == pytest.approx(0.5 * 10 + 0.1)
    br.execute([NetOrder("AAPL", -10.0, ())], {}, D)
    rep = br.deal_costs(D)
    assert rep["per_symbol"]["AAPL"]["deals"] == 2
    assert rep["total"]["commission"] == pytest.approx(10.0) and rep["total"]["swap"] == pytest.approx(0.25)
    assert MT5Broker(FakeMT5(), dry_run=True).execute([NetOrder("Z", 1.0, ())], {}, D)["Z"].commission == 0.0
