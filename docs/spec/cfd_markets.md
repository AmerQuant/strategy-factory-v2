# FX / index / metal CFDs on Moneta MT5 (path, part 3): asset class, 24x5 calendar, CFD catalogue

Code: `data/markets.py`, `costs/model.load_cost_table`, `policy/catalog.cfd_rows`, `--asset-class` in
`scripts/run_real.py`, `scripts/run_daily.py`, `scripts/run_intraday.py`. Tests: `tests/test_markets.py`.
Parts 1-2: `docs/spec/mt5_specs.md`, `docs/spec/mt5_costs.md`.

## Data
v1 store classes `fx`, `index_cfd`, `metal` (v1 `configs/universe/dukascopy.csv`; `energy_cfd` is not used):
Dukascopy mid bars, `timeframe=1H`, `adjustment=raw`, `ts` = bar start UTC (v1 `adapters/dukascopy.py`). The
equity path (`adjustment=split`) is unchanged.

## Calendar (24x5, rollover 17:00 New York)
- The whole run uses the MT5 server day: 17:00 New York = 00:00 all year (`broker_clock`, `clock_shift="ny"`).
  Any other clock shift is refused for these classes.
- Trading days are Monday to Friday of that clock (Sunday-evening bars fall on Monday). Bars that still land on a
  Saturday or Sunday are dropped and counted (`weekend_bars_dropped` in the evidence).
- Holidays: days without bars. Daily bars: `to_daily` of the hourly bars on this clock. 4H: windows anchored at its
  midnight. Swap days and DPs follow the same clock (swap: calendar days / 360, owner's choice).
- Optional `--sessions` (intraday research runs): bars outside the hand-written sessions are counted in the
  evidence (`bars_outside_sessions`); nothing is filtered by it.

## Costs
`--costs` must be a cost CSV (`scripts/convert_mt5_costs.py`); `moneta` / `flat5` are refused. `load_cost_table`
needs all five cost columns per row and has a NaN default, so a symbol without a row can never be priced; such
symbols are left out and listed (`symbols_without_costs`).

## Catalogue `2026-10-cfd-v1` (`cfd_rows`)
Every MR / TF method x direction per class (18 rows), no price / dollar-volume filters, static membership:
- FX rows `...-FX-TOP<n>` and index rows `...-IX-TOP<n>`: S7 selection of the top-N symbols by IS t-stat (t >= 1),
  `--symbol-top-n` required;
- metal rows `...-MT`: pooled (two symbols; owner's choice), `--symbol-top-n` must be 0.
Not with `--diverse`, `--membership`, `--dividends`.

## Commands (owner's machine)
```
uv run python scripts/run_real.py --store <store> --asset-class fx --timeframe 1D --symbol-top-n <n> \
    --costs costs_fx.csv --out <dir> --registry <registry.duckdb>
uv run python scripts/run_real.py --store <store> --asset-class index_cfd --timeframe 1H --resample 4h \
    --symbol-top-n <n> --costs costs_ix.csv --sessions sessions.csv --out <dir> --registry <registry.duckdb>
uv run python scripts/run_daily.py --state s.json --store <store> --asset-class fx --costs costs_fx.csv
uv run python scripts/run_intraday.py --state s.json --store <store> --asset-class metal --resample 4h --costs ...
```
Scheduling: daily job after 17:00 New York; intraday job with a `bars` trigger 00:00-23:59 New York on Sunday to
Friday - runs without a new bar do nothing.

## Not yet
- Currency translation of non-USD index CFDs (returns are in the index currency).
- Broker dividend adjustments on index CFDs; the broker's triple-swap weekday.
- Nothing here was run on the owner's store: tested on synthetic Dukascopy-layout stores only.
