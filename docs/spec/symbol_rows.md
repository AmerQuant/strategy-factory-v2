# Symbol-based rows (S7) and tradable ensemble rows

Code: `policy/ladder.select_symbols`, `policy/catalog.fx_rows / ensemble_rows`, `policy/ensemble.py`,
`data/universe.static_membership`. Tests: `tests/test_symbol_ensemble.py`.

## Symbol-based rows (FX / indices / metals)
- `fx_rows(base, top_n)`: every method × direction with no price / dollar-volume filters (volume is not meaningful for FX CFDs), no dividends, `static_membership` (a symbol is eligible from its first bar, with the usual minimum-history rule).
- **S7 selection**: after entry / exit / filter are chosen (pooled over symbols), each symbol's IS t-stat is computed with those settings; the top-N with t ≥ 1 and ≥ 10 IS trades are traded in the next OOS window. The decision record keeps the chosen symbols and every symbol's IS t-stat. Live books use the selected symbols.
- Benchmark: `symbol_ranker="random"` picks N symbols at random per DP (seeded by DP date).
- Causality: the future-perturbation test passes with symbol selection on.

Synthetic FX-like market (12 symbols, 6 mean-reverting + 6 random walk), RSI(2) buy, top 4, rung A1:
| | result |
|---|---|
| share of selections that are genuinely mean-reverting symbols | 95% |
| OOS Sharpe, selected | 2.27 |
| random-4 benchmark (20 draws) | median 1.24, p90 1.61 |
| pooled (all symbols, no selection) | 2.34 |
Selection has real skill against random-N and raises per-trade expectancy, but here it does not beat trading all symbols pooled — fewer symbols means less diversification. This is exactly the comparison the analyst must see before choosing a symbol-based row over a pooled one; both are trials.

FX / index / metal costs (spread, commission, slippage, swap per side) come from the MT5 export: `docs/spec/mt5_costs.md`; the CFD catalogue built on `fx_rows`' rules is `cfd_rows` (`docs/spec/cfd_markets.md`).

## Tradable ensemble rows (design 13.3)
- `EnsembleConfig(members)`: members of one family × direction, each with its own in-fold selection; capital split equally (capacity mode: capital / k per member; cell mode: pnl / k). One registry trial per ensemble.
- `run_row_any` dispatches ladder rows and ensembles, so the catalogue runner, robustness suite, holdout and live books handle both; live books expand an ensemble into one entry per member (`ENS-…/member`).
