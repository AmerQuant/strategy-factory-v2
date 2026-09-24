# Stage F: live decision points and forward monitoring

Code: `forward/live.py`, `forward/monitor.py`; the selector itself is `policy/ladder.decide_fold`, shared by
research, holdout and live, so a live DP makes exactly the decision research would have made (tested: the live
book at a dev DP equals the research decision for that fold).

## Live DP (design 15.1)
- `decide_book(fm, cache, bars_before_dp, membership, frozen_configs, dp)` → one `BookEntry` per row (threshold, exit, filters, eligible symbols, full decision record).
- `book_diff(old, new)` → rows added / removed / kept and per-row changes (threshold, exit, filters, symbols in/out). Removed rows only stop new entries; their open trades exit by their own rules.
- `daily_orders(cache, book, day, open_positions)` → next-open entry orders from the close of `day`: entry rule + filters on data up to `day`, one position per symbol per row, capacity and daily ranker. Orders are a superset of the engine's signals for flat symbols (tested).
- The filter random-removal test is seeded by the DP date, so research, holdout and live draw the same numbers.

## Monitoring (design 15.2–15.3), all thresholds pre-registered in `ForwardRules`
- `mc_bands(dev_daily, horizon)`: block-bootstrap percentile bands (1/5/50/95/99) of cumulative pnl and drawdown per live day.
- `cusum_lower(trade_pnl, dev_mean, dev_sd, k=0.5, h=8)`: first alarm of a downward mean shift (false-alarm rate ≤ 5% over 300 trades in tests; a 2-sd drop is caught).
- `implementation_shortfall(live, backtest)`: matched on (symbol, signal date); shortfall per trade and as a share of backtest expectancy; missed trades counted.
- `evaluate_forward(...)` → `stop` (drawdown beyond p99, CUSUM alarm, shortfall > 50%), `promote` (≥ 126 days or ≥ 40 trades, cumulative pnl above the p5 band, shortfall ≤ 25%), else `continue` with reasons.

## Not yet built
Broker bridge (MT5 orders/fills, position netting across rows, reconciliation) — P11; needs the owner's environment.
