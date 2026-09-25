# Diverse edge families (design 13.2) - package 3

Code: `signals/methods.py` (methods, `SeriesCtx`), `signals/specs.py` (BRK and HOLD exit libraries,
`neutral_exit_for` / `exit_library_for`), `engine/cache.py` (`ctx`, `set_index_events`), `policy/ladder.py`
(per-method exits), `policy/catalog.py` (`diverse_rows`, taxonomy), `forward/live.py` (context in daily orders),
`scripts/run_real.py --diverse`. Tests: `tests/test_families.py`, `tests/test_methods.py`.

## Rows
| method | family | idea | parameter grid | default | exit style (neutral) | directions |
|---|---|---|---|---|---|---|
| vol_spike | VOL | fade a range-expansion bar (true range / previous ATR20 >= p) that closes at its extreme against the move | 1.5, 2, 2.5, 3 | 2 | MR (prev_high) | buy, sell |
| squeeze | VOL | breakout of the 20-bar range after a volatility contraction (previous-bar ATR% percentile <= p) | 0.1 .. 0.3 | 0.2 | BRK (3 ATR trailing) | buy, sell |
| xs_mom | XS | first bar of each month; score = momentum over p months skipping the last month; the capacity ranker keeps the strongest cross-section | 3, 6, 9, 12 months | 12 | HOLD (21 bars) | buy (winners), sell (losers) |
| tom | CAL | turn of the month: signal when p weekdays are left in the month (fixed calendar, known in advance) | 1 .. 4 | 2 | HOLD (5 bars) | buy |
| post_exdiv | EV | enter on the close of the ex-date (the ex-day move is known then) | - | - | HOLD (5 bars) | buy |
| index_add | EV | enter on the first bar of an index membership spell (effective date) | - | - | HOLD (10 bars) | buy |

Exit libraries: BRK = trailing 2 / 3 / 4 ATR, time 20, time 40 with a 2 ATR stop; HOLD = time 3 / 5 / 10 / 21 / 42.
The A3 rung chooses from them exactly as for MR / TF rows.

## Context (`SeriesCtx`)
Methods that need more than prices get the bar dates, the adjusted open, dividends on the first bar of the
ex-date and index-addition flags. Every field is known at bar t; `upto(i)` truncates it for live decisions.
A context method called without its context raises instead of silently producing no trades.

## Notes and rules
- **XS needs a capacity limit.** Without `max_positions` every symbol is bought every month; `diverse_rows`
  raises `max_positions` to 10 for XS rows when the base has none.
- **index_add rows use the top-liquidity universe.** A symbol added to the index mid-fold is not an index member
  at the DP, so the membership universe would never see the event. The event itself comes from the membership
  file (effective date), which is point-in-time; announcement dates would allow an earlier entry and are not
  modelled.
- **Event rows are only created when their data exists** (`dividends=True`, `index_events=True`), so rows with
  no possible trade never add trials to the registry.
- Separate catalogue version `DIVERSE_CATALOG_VERSION = "2026-10-families-v1"`; the 18-row catalogue is unchanged.
- run_real: `--diverse` adds the rows; family ensembles are built only for families with at least two members.

## Evidence on synthetic data (A1, 20 symbols, seed 11)
| market | vol_spike t | squeeze t | tom t | xs_mom t |
|---|---|---|---|---|
| random walk | 1.11 | -0.29 | -1.38 | -0.71 |
| mean reverting | **8.00** | -1.38 | -1.65 | -0.92 |
| trending | -0.27 | **2.77** | -1.77 | **2.33** |
| persistent per-symbol drifts (XS test market) | | | | **3.96** (Sharpe 1.49) |

Each family finds its edge where it exists and nothing on the random walk; xs_mom with the score ranker earns more
than twice the random ranker on the drift market, and its sell side (short the losers) is profitable too.
Calendar and event effects have no synthetic analogue here: they are tested for mechanics (firing days,
causality) and wait for real data.

## Not yet
- Gap rows (open vs previous close) need a same-bar execution model (signal at the open); left out.
- Announcement-date events (earnings, index announcements) need an event calendar with announcement times.
