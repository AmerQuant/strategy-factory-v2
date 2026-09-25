import multiprocessing as mp
from dataclasses import replace

from conftest import build

from sfactory.data.regime import market_up_series
from sfactory.engine.cache import TradeCache
from sfactory.engine.parallel import precompute, row_specs
from sfactory.policy.ensemble import family_ensemble_row
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.signals.specs import STRUCTURAL_MARKET_UP

ROWS = [LadderConfig(rung="A4", method="rsi"),
        LadderConfig(rung="A3", method="donchian_break", direction=-1),
        LadderConfig(rung="A1", method="xs_mom", max_positions=5),
        LadderConfig(rung="A1", method="ibs", structural=(STRUCTURAL_MARKET_UP,))]


def _market(tag):
    fm, cache, dev, mem = build("mean_revert", seed=21, n_symbols=8)
    cache.data_version = tag
    cache.set_market_regime(*market_up_series(dev), "eqw-ma200")
    return fm, cache, dev, mem


def test_row_specs_cover_the_ladder():
    rsi = LadderConfig(rung="A4", method="rsi")
    n = len(rsi.grid) * len(rsi.exit_lib) * (1 + len(rsi.filters))
    assert len(row_specs(rsi)) == n
    assert len(row_specs(LadderConfig(rung="A0", method="rsi"))) == 1
    ens = family_ensemble_row([LadderConfig(rung="A1", method=m) for m in ("rsi", "ibs")], "MR", 1)
    assert len(row_specs(ens)) == len(rsi.grid) + len(LadderConfig(method="ibs").grid)


def test_precompute_makes_the_research_run_all_hits_and_identical():
    fm, cold, dev, mem = _market("par")
    ref = [run_ladder(fm, cold, dev, mem, r) for r in ROWS]
    fm, warm, dev, mem = _market("par")
    info = precompute(warm, ROWS, n_workers=2)
    assert info["frames_added"] > 0
    before = warm.computed
    got = [run_ladder(fm, warm, dev, mem, r) for r in ROWS]
    assert warm.computed == before                              # every frame came from the precompute
    for a, b in zip(ref, got):
        assert a.decisions == b.decisions and a.oos_trades.equals(b.oos_trades)
    assert precompute(warm, ROWS, n_workers=2)["frames_added"] == 0   # idempotent


def test_worker_count_and_spawn_do_not_change_results():
    c1 = _market("w")[1]
    precompute(c1, ROWS[:2], n_workers=1)
    c3 = _market("w")[1]
    precompute(c3, ROWS[:2], n_workers=3, mp_context=mp.get_context("spawn"))    # explicit = the default
    assert set(c1._store) == set(c3._store)
    assert all(c1._store[k].equals(c3._store[k]) for k in c1._store)


def test_precompute_writes_parquet_for_the_next_run(tmp_path):
    fm, c, dev, mem = _market("disk")
    disk = TradeCache(c.arrays, "disk", cache_dir=tmp_path)
    disk.set_market_regime(c._regime_dates, c._regime_flags, c._regime_id)
    precompute(disk, ROWS[:1], n_workers=2)
    again = TradeCache(c.arrays, "disk", cache_dir=tmp_path)
    again.set_market_regime(c._regime_dates, c._regime_flags, c._regime_id)
    r = run_ladder(fm, again, dev, mem, replace(ROWS[0], rung="A1"))
    assert again.computed == 0 and again.loaded > 0 and r.stats["n"] > 0
