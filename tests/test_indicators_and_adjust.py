import numpy as np
import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.signals.indicators import rsi_wilder


@settings(max_examples=40, deadline=None)
@given(st.integers(10, 200), st.integers(0, 10_000))
def test_rsi_is_causal(cut, seed):
    rng = np.random.default_rng(seed)
    x = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 250)))
    y = x.copy()
    y[cut:] *= rng.uniform(0.5, 1.5, 250 - cut)
    a, b = rsi_wilder(x, 2), rsi_wilder(y, 2)
    assert np.array_equal(a[:cut], b[:cut], equal_nan=True)


def test_adjusted_series_removes_dividend_drop_crsp_style():
    """Backward adjustment identity: adj return on ex-date = close_i / (close_{i-1} - dividend)."""
    bars, divs, _ = make_market(3, 400, seed=1)
    adj = add_adj_factor(bars, divs)
    for row in divs.head(5).iter_rows(named=True):
        g = adj.filter(pl.col("symbol") == row["symbol"]).sort("date")
        i = g["date"].to_list().index(row["ex_date"])
        c, f = g["close"].to_numpy(), g["adj_factor"].to_numpy()
        assert np.isclose(c[i] * f[i] / (c[i - 1] * f[i - 1]), c[i] / (c[i - 1] - row["amount"]))
