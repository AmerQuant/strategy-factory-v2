from conftest import build

from sfactory.evaluation.ablation import run_ablation
from sfactory.policy.ladder import LadderConfig


def test_ablation_accepts_only_significant_improvements():
    fm, cache, dev, mem = build("random_walk", seed=11)
    rw = run_ablation(fm, cache, dev, mem, LadderConfig(max_positions=5, max_new_per_day=2))
    assert [r["accepted"] for r in rw["table"]][1:] == [False, False, False]
    assert 0.0 <= rw["pbo"]["pbo"] <= 1.0
    fm, cache, dev, mem = build("mean_revert", seed=11)
    mr = run_ablation(fm, cache, dev, mem, LadderConfig(max_positions=5, max_new_per_day=2), rungs=("A0", "A1"))
    assert mr["table"][1]["accepted"]
