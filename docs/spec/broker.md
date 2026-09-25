# Broker bridge and paper mode - package 6

Code: `broker/orders.py` (contract, netting, row books), `broker/sim.py` (simulated broker),
`broker/reconcile.py`, `broker/mt5.py` (MetaTrader 5 adapter), `forward/paper.py` (parity planner, daily loop).
Tests: `tests/test_paper.py`, `tests/test_mt5_adapter.py`.

## Daily cycle
1. **Close of day t - plan** (`plan_day`): for every book row, exits for its open virtual positions and entries
   for eligible symbols, then capacity (`max_positions`, `max_new_per_day`, score ranking) and quantities
   (capital / max_positions or cache notional, times the vol weight for `sizing="vol"` rows, rounded to the lot step).
2. **Netting** (`net_orders`): one net order per symbol across rows; opposite row orders cross inside the book.
3. **Open of t+1 - execute** at the broker (simulated or MT5); **allocate** fills back to row orders (broker price
   and pro-rata commission for the traded part, open price and no cost for the crossed part).
4. **Row books** update (virtual positions, closed trades in the research trade format); **reconcile**: the sum of
   the rows' positions must equal the broker's net positions for our magic number.

## Research parity (the central guarantee)
The planner does not re-implement any rule. It appends one placeholder bar (next business day, open = close =
today's close) to the arrays known at today's close and asks the research `TradeCache` for trades:
- a trade with `signal_date == today` is an entry for the next open;
- an open position exits when its research trade (same `signal_date`) exits at the placeholder open without the
  forced end-of-data flag, i.e. its exit rule fired at today's close.
Tested: replaying 18 months day by day through the simulated broker reproduces the research trades exactly
(signal, entry and exit dates, fill prices, per-share net pnl) for an MR row with the neutral exit, an MR row
with an ATR stop and a TF row with a trailing stop; perturbing bars after the planning day never changes the plan.

## MT5 adapter
Official `MetaTrader5` package (Windows), imported lazily. Positions are ours by `magic` number; in a hedging
account a reducing order closes our positions by ticket (oldest first) before opening the remainder, so the
account holds one net position per symbol as the books assume. Volumes are rounded down to `volume_step` and
skipped below `volume_min`. `dry_run=True` builds requests without sending them. `symbol_map` maps research
symbols to broker symbols. Tested against a fake terminal only (no Windows / MT5 in the development environment).

## Cash flows
- Dividends: every position held at the prior close of an ex-date gets qty x dividend (short pays), accrued
  before the day's fills, exactly as the research engine credits them.
- Swap: the modelled financing (cost model rate / day count x calendar days held) is booked on close.
- Tested: paper trades equal research trades per share including dividends and swap on a dividend market.
- MT5: fills carry the real commission + fee from the deal history; `deal_costs(day)` and `open_swap()` report
  realised commission / fee / swap for the implementation-shortfall comparison (the books keep modelled costs).

## Not yet
- The first live connection test on the owner's machine (daily job: docs/spec/daily.md).
- Intraday planning (the placeholder bar is the next business day).
