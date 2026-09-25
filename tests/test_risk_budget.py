import numpy as np
import polars as pl
import pytest

from sfactory.portfolio.combine import RiskBudget, apply_budget, cap_weights, combine_rows


def _cal(n):
    c = pl.date_range(pl.date(2012, 1, 2), pl.date(2030, 1, 1), "1d", eager=True)
    return c.filter(c.dt.weekday() <= 5).to_numpy()[:n]


def _panel(seed=0, t=1500):
    rng = np.random.default_rng(seed)
    base = rng.normal(0, 1, (t, 1))
    x = np.column_stack([
        30 + 400 * rng.normal(0, 1, t),                    # 0 MR, low vol
        20 + 300 * rng.normal(0, 1, t),                    # 1 MR
        25 + 350 * rng.normal(0, 1, t),                    # 2 MR
        40 + 1500 * rng.normal(0, 1, t),                   # 3 TF, high vol
        10 + 200 * (0.5 * base[:, 0] + rng.normal(0, 1, t)),   # 4 VOL
    ])
    return x, ["MR", "MR", "MR", "TF", "VOL"]


def test_cap_weights_water_filling():
    w = cap_weights({0: 0.7, 1: 0.2, 2: 0.1}, 0.4)
    assert w[0] == pytest.approx(0.4) and sum(w.values()) == pytest.approx(1.0)
    assert w[1] / w[2] == pytest.approx(2.0)                    # excess spread pro rata
    g = cap_weights({0: 0.3, 1: 0.3, 2: 0.2, 3: 0.2}, None, {0: "A", 1: "A", 2: "B", 3: "C"}, 0.5)
    assert g[0] + g[1] == pytest.approx(0.5) and sum(g.values()) == pytest.approx(1.0)
    cash = cap_weights({0: 0.5, 1: 0.5}, 0.3)                    # every row capped: the rest stays in cash
    assert sum(cash.values()) == pytest.approx(0.6)
    both = cap_weights({0: 0.4, 1: 0.3, 2: 0.2, 3: 0.1}, 0.3, {0: "A", 1: "A", 2: "B", 3: "B"}, 0.5)
    assert max(both.values()) <= 0.3 + 1e-9 and both[0] + both[1] <= 0.5 + 1e-9
    assert sum(both.values()) == pytest.approx(1.0) and both[2] + both[3] == pytest.approx(0.5)


def test_vol_target_hits_the_ex_ante_target():
    x, fams = _panel()
    hist = x[:500]
    w = {i: 0.2 for i in range(5)}
    b = RiskBudget(target_vol_ann=0.05, capital=100_000, lev_max=10)
    w2, lev = apply_budget(w, hist, b, fams)
    vec = np.array([w2[i] for i in range(5)]) * lev
    sd = np.sqrt(vec @ np.cov(hist, rowvar=False) @ vec)
    assert sd == pytest.approx(0.05 * 100_000 / np.sqrt(252))


def test_combined_policy_with_budget():
    x, fams = _panel()
    d = _cal(len(x))
    starts, ends = list(d[500::120][:-1]), list(d[620::120])
    free = combine_rows(x, d, starts, ends, corr_cap=0.9)
    same = combine_rows(x, d, starts, ends, corr_cap=0.9, budget=RiskBudget(), families=fams)
    assert np.array_equal(free.daily, same.daily)               # no budget -> unchanged
    b = RiskBudget(max_weight=0.3, family_cap=0.5, target_vol_ann=0.08, lev_max=3.0)
    c = combine_rows(x, d, starts, ends, corr_cap=0.9, budget=b, families=fams)
    for w, lev in zip(c.weights, c.leverage):
        raw = {k: v / lev for k, v in w.items()}
        assert max(raw.values()) <= 0.3 + 1e-9
        assert sum(v for k, v in raw.items() if fams[k] == "MR") <= 0.5 + 1e-9
    realised = c.daily[500:].std(ddof=1) * np.sqrt(252) / 100_000
    assert 0.06 < realised < 0.10                                # the ex-ante target carries over
    x2 = x.copy()
    x2[900:] *= 3.0                                               # the future changes; earlier folds must not
    c2 = combine_rows(x2, d, starts, ends, corr_cap=0.9, budget=b, families=fams)
    k = sum(1 for s in starts if s <= d[900])
    assert c.weights[:k] == c2.weights[:k] and c.leverage[:k] == c2.leverage[:k]


def test_catalogue_combined_policy_under_a_budget():
    from conftest import build

    from sfactory.evaluation.catalog_runner import run_catalog
    from sfactory.policy.ladder import LadderConfig
    from sfactory.registry.repo import Registry
    fm, cache, dev, mem = build("mean_revert", seed=11, n_symbols=8)
    rows = [LadderConfig(rung="A1", method=m, max_positions=4) for m in ("rsi", "ibs", "consec", "ma_cross")]
    free = run_catalog(fm, cache, dev, mem, rows, registry=Registry(), spa_boot=50)
    b = RiskBudget(max_weight=0.4, family_cap=0.7, target_vol_ann=0.05)
    capped = run_catalog(fm, cache, dev, mem, rows, registry=Registry(), spa_boot=50, budget=b)
    assert free["combined"]["risk_budget"] is None and capped["combined"]["risk_budget"]["max_weight"] == 0.4
    assert len(capped["accepted"]) >= 2
    for w, lev in zip(capped["combined"]["weights_per_fold"], capped["combined"]["leverage_per_fold"]):
        raw = {k: v / lev for k, v in w.items()} if lev > 0 else w
        assert not raw or max(raw.values()) <= 0.4 + 1e-6
        mr = sum(v for k, v in raw.items() if k.startswith("MR-"))
        assert mr <= 0.7 + 1e-6
