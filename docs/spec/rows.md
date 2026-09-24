# Rows: entry methods, directions, TF exits, catalogue

Code: `signals/methods.py`, `signals/specs.py`, `engine/generic.py` (trailing stop), `policy/ladder.py`,
`policy/catalog.py`. Any method runs through the same A0→A4 ladder; grid, default, neutral exit and exit library
follow the method family.

| Family | Method | Entry (buy; sell is mirrored) | Grid |
|---|---|---|---|
| MR | rsi | RSI(2) < p | 5–25 |
| MR | ibs | (C−L)/(H−L) < p | 0.10–0.30 |
| MR | consec | ≥ p consecutive lower closes | 2–5 |
| MR | lowest_close | close below the lowest of the previous p−1 closes | 3–15 |
| MR | donchian_low | low touches the p-bar low channel | 5–30 |
| TF | ma_cross | close crosses above SMA(p) | 20–200 |
| TF | donchian_break | close above the p-bar high | 20–100 |
| TF | supertrend | flip to up-trend, ATR(10) × p | 1.5–4 |
| TF | ichimoku | close above cloud and tenkan > kijun (scale p × 9/26/52) | 0.5–2 |

All rules are causal and scale-invariant (tested for every method and direction).
TF exit library: reverse (neutral: close below SMA / below p/2-bar low / supertrend flip / close below kijun),
3 or 2 ATR trailing stop, reverse + 2 ATR stop, time 20 / 40. Trend-side filters mirror for sells.
Daily-ranking score: MR = oversold depth (RSI, IBS, N-day return); TF = 20-day momentum.
Catalogue: 9 methods × 2 directions = 18 pre-registered equity rows (`CATALOG_VERSION`), each with taxonomy labels.

## Sanity matrix (synthetic, 30 symbols, rung A1, OOS trade t-stat)
| data | MR rows (10) | TF rows (8) |
|---|---|---|
| mean-reverting | +6.4 … +12.4 | −6.2 … −1.3 |
| trending | −5.7 … −1.3 | +2.7 … +4.8 |
| random walk | −2.6 … −0.5 | −2.7 … −0.3 |
Each family finds edge only in the regime where it exists; on random walk no row reaches t = 2.
