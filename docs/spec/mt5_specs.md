# MT5 symbol specifications and sessions (FX / index / metals path, part 1)

Code: `broker/mt5_specs.py`, `data/sessions.py`, `scripts/export_mt5_specs.py`. Tests: `tests/test_mt5_specs.py`
(fake terminal; not yet run against a live Moneta terminal).

## Export (owner's machine, Windows)
```
set SF_MT5_PASSWORD=...
uv run python scripts/export_mt5_specs.py --symbol-map broker_symbols.csv --out mt5_specs.csv \
    --mt5-login <login> --mt5-server <server> [--timeframe H1 --bars 2000]
```
One row per symbol from `symbol_info`: contract size, point / digits, tick size and tick value (profit / loss),
volume min / step / max, base / profit / margin currency, account (deposit) currency, calculation mode, swap long /
short, swap mode, triple-swap weekday (`swap_rollover3days`, raw MT5 value), current spread.
Enumerations are written as the names of the package's constants (`SYMBOL_SWAP_MODE_POINTS` -> `points`), found by
value at run time, so no constant value is hard-coded. A symbol the terminal does not know is skipped and reported.

## Spread (owner's choice: the broker's bar history)
From the `spread` field (points) of the last `--bars` bars of `copy_rates_from_pos`: per bar
`spread * point / close * 1e4` bps; the CSV keeps mean and median (bps and points), the bar count, bars with zero
spread, the history span and the median close of the same bars (`ref_close_median`, the reference price of the cost
conversion). Not verified: which statistic MT5 stores per bar in `spread` - check on the terminal.

## What the export cannot give
- ECN commission per lot and slippage: manual file (part 2, `costs/mt5_costs.py`).
- Sessions: the Python package has no session function (owner's choice: a hand-written file).

## Sessions file
`symbol,weekday,start,end` on the broker clock (00:00 = 17:00 New York, the MT5 server day); several rows per day
for breaks; `end` may be 24:00. Validated (weekday names, end after start, no overlaps). `bars_outside(bars,
sessions)` counts intraday bars that start outside the declared session and lists symbols without sessions. Nothing
is inferred from the data.
