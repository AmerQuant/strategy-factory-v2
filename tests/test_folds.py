from datetime import date
from itertools import pairwise

import polars as pl
import pytest
from conftest import CFG

from sfactory.timeline.folds import FoldManager, HoldoutLockedError


def test_dev_folds_contiguous_and_before_holdout():
    folds = FoldManager(CFG).dev_folds()
    assert folds[0].dp == CFG.first_dp
    for a, b in pairwise(folds):
        assert a.oos_end == b.dp
    assert folds[-1].oos_end == CFG.holdout_start
    assert all(not f.holdout and f.is_start < f.dp for f in folds)


def test_holdout_locked_by_default():
    fm = FoldManager(CFG)
    with pytest.raises(HoldoutLockedError):
        fm.holdout_folds()
    assert fm.holdout_folds(unlock=True)[0].dp == CFG.holdout_start


def test_is_slice_purges_trades_crossing_dp():
    fm = FoldManager(CFG)
    f = fm.dev_folds()[1]
    assert f.dp == date(2013, 7, 1)
    trades = pl.DataFrame({"signal_date": [date(2013, 6, 27), date(2013, 6, 27)],
                           "entry_date": [date(2013, 6, 28), date(2013, 6, 28)],
                           "exit_date": [date(2013, 6, 28), date(2013, 7, 2)]})
    assert len(fm.slice_is(trades, f)) == 1


def test_dev_view_hides_holdout():
    fm = FoldManager(CFG)
    df = pl.DataFrame({"date": [date(2017, 12, 29), date(2018, 1, 2)]})
    assert fm.dev_view(df)["date"].max() < CFG.holdout_start
