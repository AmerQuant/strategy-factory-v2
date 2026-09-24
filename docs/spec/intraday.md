# Intraday timeframes (1H, 4H) - package 2

Code: `data/resample.py`, `data/synthetic.make_intraday_market`, and intraday handling in `data/adjust.py`,
`engine/cache.py`, `data/universe.py`, `metrics/core.py`, `portfolio/capacity.py`, `evaluation/ablation.py`,
`evaluation/robustness.py`, `data/folds_from_data.py`, `data/sfac_store.py`, `scripts/run_real.py`.
Tests: `tests/test_intraday.py`.

## Time convention
- Daily bars: `date` is a `pl.Date`. Intraday bars: `date` is a naive `pl.Datetime("us")` = bar START in the
  run's clock (UTC unless shifted). Trade frames keep the bar dtype (`signal_date`, `entry_date`, `exit_date`).
- DPs stay calendar dates (00:00 of the clock). Every "before the DP" rule compares bar starts with the DP, so
  **no bar may straddle midnight of the clock** (`check_no_straddle`), otherwise the last bar before a DP would
  close after it. Hourly bars never straddle; 4H bars do unless anchored to the clock's midnight.
- The engine is bar-index based and unchanged: signal on the close of bar t, fill at the open of bar t+1.
  All bar-count parameters (RSI period, MA length, max_hold, ATR/percentile windows) are in bars of the row's
  timeframe, as in the TradingView suite.

## Broker alignment (design: 4H from 1H with broker alignment + sensitivity test)
- `resample_bars(bars, "4h", clock_shift)` groups 1H bars into windows anchored at midnight of the shifted clock
  (label = window start). `clock_shift="7h"` puts 17:00 New York (winter, UTC-5) at 00:00, the MT5 server day.
- The shifted clock is the clock of the whole run: DPs, calendar days for swap and the daily pnl calendar.
- `alignment_variants(bars, "4h", ("0h","1h","2h","3h"))` is the sensitivity set; each variant is a new data
  version (run_real appends `-4h-shift<k>h`).
- `to_daily` builds daily bars from intraday bars on the clock's calendar day.

## What changes per component
| component | intraday rule |
|---|---|
| dividend adjustment | ex-date = first bar of that calendar day; earlier bars scaled with the close of the last bar before it |
| dividend cash flow | credited on the first bar of the ex-date when held across it |
| swap | notional x rate / 360 x calendar-day boundaries crossed (0 for a trade opened and closed the same day) |
| universe (S1) | intraday bars aggregated to days first (last close, summed volume): price, dollar volume and `min_history` mean trading days on every timeframe |
| capacity (S8) | `max_new_per_day` is a calendar-day budget shared by all intraday bars of that day |
| metrics / ablation / catalogue / holdout | realised pnl by exit calendar weekday, so daily and intraday rows share one daily calendar and Sharpe x sqrt(252) |
| robustness regime labels | joined on the bar timestamp dtype |
| folds from data | datetimes accepted; DPs are dates |
| v1 store | 1H snapshots read with `ts` converted to naive UTC |

## Evidence on synthetic data
- hourly MR market (7 bars/day): the A1 RSI row finds the edge (Sharpe > 1); the hourly random walk does not.
- future-perturbation test passes on hourly bars (parameters, universe and monthly activation at past DPs).
- `always` activation equals `run_ladder` on hourly bars; a 4H series resampled from 1H runs the same pipeline.

## One command
```
uv run python scripts/run_real.py --store <store> --timeframe 1H --out <dir> --registry <registry.duckdb>
uv run python scripts/run_real.py --store <store> --timeframe 1H --resample 4h --clock-shift 7h --out <dir> ...
```

## Not yet
- US-equity sessions: overnight gaps between the last and first hourly bar are ordinary bar-to-bar moves; a
  session-aware "exit before close" rule is a later exit-library addition.
- DST: a fixed `clock_shift` is exact for half the year; a DST-aware broker clock is a later addition.
- `forward.live.daily_orders` still assumes one decision per day.
