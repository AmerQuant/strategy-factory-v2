from datetime import date

import numpy as np
from conftest import build

from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.portfolio.combine import combine_rows, effective_n, family_ensemble
from sfactory.registry.repo import Registry
from sfactory.stats.core import sharpe_diff_ci


def _cal(n):
    return np.arange(np.datetime64("2015-01-01"), np.datetime64("2015-01-01") + np.timedelta64(n, "D"), dtype="datetime64[D]")


def test_effective_n_and_ensemble():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, (2000, 5))
    assert 4.5 < effective_n(x) <= 5.0
    same = np.repeat(x[:, :1], 5, axis=1)
    assert abs(effective_n(same) - 1.0) < 1e-6
    assert np.allclose(family_ensemble(x, [0, 2]), (x[:, 0] + x[:, 2]) / 2)


def test_combine_is_causal_respects_cap_and_normalises():
    rng = np.random.default_rng(1)
    base = rng.normal(0.05, 1, (1500, 1))
    x = np.hstack([base + rng.normal(0, 0.2, (1500, 1)), base + rng.normal(0, 0.2, (1500, 1)),
                   rng.normal(0.05, 1, (1500, 2))])
    d = _cal(1500)
    starts = [date(2015, 1, 1) + (date(2015, 7, 1) - date(2015, 1, 1)) * k for k in range(8)]
    ends = starts[1:] + [date(2019, 2, 1)]
    c = combine_rows(x, d, starts, ends, corr_cap=0.6)
    for w, sel in zip(c.weights, c.selected):
        assert abs(sum(w.values()) - 1) < 1e-9 or not w
        assert not ({0, 1} <= set(sel)) or len(sel) == 4  # correlated pair never both (except warm-up)
    y = x.copy()
    k = 4
    y[int(np.searchsorted(d, np.datetime64(starts[k]))):] *= -3
    c2 = combine_rows(y, d, starts, ends, corr_cap=0.6)
    assert c.weights[: k + 1] == c2.weights[: k + 1]


def test_sharpe_diff_ci_detects_better_series():
    rng = np.random.default_rng(2)
    b = rng.normal(0, 1, 2000)
    lo, hi = sharpe_diff_ci(b + 0.3, b)
    assert lo > 0
    lo, hi = sharpe_diff_ci(b, b)
    assert lo <= 0 <= hi


def test_catalog_runner_screens_and_counts_trials():
    base = LadderConfig(max_positions=5, max_new_per_day=2)
    fm, cache, dev, mem = build("random_walk", seed=11)
    reg = Registry()
    rw = run_catalog(fm, cache, dev, mem, equity_rows(base), registry=reg)
    assert rw["n_trials"] == reg.count_trials() == 18
    assert rw["accepted"] == []
    fm, cache, dev, mem = build("mean_revert", seed=11)
    mr = run_catalog(fm, cache, dev, mem, equity_rows(base))
    fams = {mr["table"][i]["family"] for i in mr["accepted"]}
    assert fams == {"MR"} and len(mr["accepted"]) >= 5
    assert mr["combined"]["sharpe"] > mr["benchmark_all_equal"]


def test_cost_breakdown_uses_the_same_trades():
    from sfactory.costs.model import CostModel
    from sfactory.evaluation.evidence import build_evidence
    from sfactory.report.html import render_html
    fm, cache, dev, mem = build("mean_revert", seed=11)
    cache.cost_model = CostModel.moneta_share_cfd_proxy()
    rows = [r for r in equity_rows(LadderConfig(max_positions=5, max_new_per_day=2)) if r.method == "rsi"]
    rep = run_catalog(fm, cache, dev, mem, rows)
    for t, r in zip(rep["table"], rep["results"]):
        tr = r.oos_trades
        assert abs(t["pnl_gross"] - t["cost_trading"] - t["cost_swap"] - t["pnl_net"]) < 1e-6 * max(1, abs(t["pnl_gross"]))
        assert abs(t["pnl_net"] - float(tr["net_pnl"].sum())) < 1e-6 * max(1.0, abs(t["pnl_net"]))
        assert t["cost_trading"] > 0 and t["cost_swap"] > 0            # the proxy charges both
        assert t["sharpe_gross"] > t["sharpe_no_swap"] > t["sharpe"]     # costs only lower the result
    pkg = build_evidence(rep, None, {"data": "syn"})
    assert pkg["benchmark_all_rows_equal_dev_gross"] > pkg["benchmark_all_rows_equal_dev"]
    assert len(pkg["dev_curve"]["benchmark_gross"]) == len(pkg["dev_curve"]["benchmark"])
    html = render_html(pkg)
    assert "اثر هزینه‌ها" in html and "شارپ بدون هزینه" in html and "وزن برابر، بدون هزینه" in html
