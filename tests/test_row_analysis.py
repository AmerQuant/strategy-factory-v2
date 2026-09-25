import json

from conftest import build

from sfactory.evaluation.row_analysis import analyze_rows, summarize
from sfactory.policy.ensemble import family_ensemble_row
from sfactory.policy.ladder import LadderConfig
from sfactory.registry.repo import Registry


def test_accepted_rows_get_edge_and_sizing_analysis_as_trials():
    fm, cache, dev, mem = build("mean_revert", seed=11, n_symbols=8)
    rows = [LadderConfig(rung="A1", method="rsi", max_positions=4)]
    ens = family_ensemble_row([LadderConfig(rung="A1", method=m) for m in ("rsi", "ibs")], "MR", 1)
    reg = Registry()
    out = analyze_rows(fm, cache, dev, mem, rows + [ens], reg, n_boot=200)
    a = out["MR-RSI-BUY-EQ"]
    assert a["edge_state"]["persistence"]["verdict"] in ("persistent", "not_persistent")
    assert a["edge_state"]["table"][0]["mode"] == "always"
    assert a["sizing"]["table"][0]["variant"] == "fixed"
    assert "skipped" in out[ens.rid]
    assert reg.count_trials() == 5 + 6                  # five activation modes + six sizing variants
    json.dumps(out)                                      # JSON-safe for the evidence package
    s = summarize(out)
    assert set(s) == {"MR-RSI-BUY-EQ"} and "persistent" in s["MR-RSI-BUY-EQ"]
