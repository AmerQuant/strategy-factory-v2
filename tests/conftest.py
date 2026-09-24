from datetime import date

import pytest

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.timeline.folds import FoldConfig, FoldManager

CFG = FoldConfig(data_start=date(2010, 1, 4), first_dp=date(2013, 1, 1), holdout_start=date(2018, 1, 1),
                 data_end=date(2020, 1, 1), dp_months=6, is_years=3)


def build(kind="random_walk", seed=7, n_symbols=20, bars_transform=None):
    bars, divs, mem = make_market(n_symbols, 2600, seed=seed, kind=kind)
    if bars_transform is not None:
        bars = bars_transform(bars)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), f"syn-{kind}-{seed}")
    return fm, cache, dev, mem


@pytest.fixture(scope="session")
def rw_market():
    return build("random_walk")
