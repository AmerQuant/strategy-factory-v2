import math

import numpy as np

from sfactory.stats.core import (
    bootstrap_ci,
    deflated_sharpe,
    expected_max_sharpe,
    min_track_record_length,
    moments,
    probabilistic_sharpe,
    sharpe_report,
)


def test_psr_is_calibrated_under_the_null():
    """If the true SR equals the benchmark, PSR > 0.95 should happen ~5% of the time."""
    rng = np.random.default_rng(0)
    hits = 0
    trials = 2000
    for _ in range(trials):
        x = rng.normal(0.05, 1.0, 250)  # true per-period SR = 0.05
        sr, sk, ku, n = moments(x)
        hits += probabilistic_sharpe(sr, 0.05, n, sk, ku) > 0.95
    assert 0.03 < hits / trials < 0.07


def test_expected_max_sharpe_matches_monte_carlo():
    rng = np.random.default_rng(1)
    for n_trials in (10, 100, 1000):
        mc = rng.standard_normal((4000, n_trials)).max(axis=1).mean()
        assert abs(expected_max_sharpe(n_trials, 1.0) - mc) / mc < 0.04
    assert expected_max_sharpe(1, 1.0) == 0.0


def test_dsr_penalises_more_trials_and_min_trl_is_consistent():
    sr, n = 0.1, 500
    assert deflated_sharpe(sr, n, 0, 3, 1, 0.002) > deflated_sharpe(sr, n, 0, 3, 100, 0.002)
    m = min_track_record_length(sr, 0.0, 0, 3, alpha=0.05)
    assert math.isclose(probabilistic_sharpe(sr, 0.0, math.ceil(m), 0, 3), 0.95, abs_tol=0.01)
    assert min_track_record_length(0.0, 0.1) == math.inf


def test_bootstrap_ci_covers_mean_and_block_is_wider_on_autocorrelated_data():
    rng = np.random.default_rng(2)
    x = rng.normal(1.0, 1.0, 400)
    lo, hi = bootstrap_ci(x, n_boot=1000)
    assert lo < 1.0 < hi
    e = rng.normal(0, 1, 2000)
    ar = np.empty_like(e)
    ar[0] = e[0]
    for t in range(1, len(e)):
        ar[t] = 0.8 * ar[t - 1] + e[t]
    w_iid = np.subtract(*bootstrap_ci(ar, n_boot=500)[::-1])
    w_blk = np.subtract(*bootstrap_ci(ar, n_boot=500, block=20)[::-1])
    assert w_blk > 1.5 * w_iid


def test_sharpe_report_fields():
    rep = sharpe_report(np.random.default_rng(3).normal(0.1, 1, 300), n_trials=10)
    assert {"psr_vs_0", "dsr", "min_trl_days", "sr_annual"} <= rep.keys()
    assert rep["dsr"] <= rep["psr_vs_0"]
