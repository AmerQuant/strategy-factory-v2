import io

from conftest import build

from sfactory.engine.parallel import precompute
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.progress import Progress, fmt_seconds


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_counter_throttles_and_estimates_the_remaining_time():
    clk, out = Clock(), io.StringIO()
    p = Progress(stream=out, min_interval=10, clock=clk, enabled=True)
    c = p.counter("rows", 4)
    clk.t = 5
    c.tick(label="A")                 # first step: printed
    clk.t = 8
    c.tick(label="B")                 # 3 s later: throttled
    clk.t = 20
    c.tick(label="C")                 # 15 s since the last line: printed, 3 of 4 in 20 s -> eta 6.7 s
    clk.t = 21
    c.tick(label="D")                 # last step: always printed
    lines = out.getvalue().splitlines()
    assert lines == ["[progress] rows 1/4 (25.0%) A elapsed 0m05s eta 0m15s",
                     "[progress] rows 3/4 (75.0%) C elapsed 0m20s eta 0m06s",
                     "[progress] rows 4/4 (100.0%) D elapsed 0m21s"]
    p.stage("done")
    assert out.getvalue().splitlines()[-1] == "[progress] stage: done (elapsed 0m21s)"
    assert fmt_seconds(3725) == "1h02m"


def test_progress_can_be_silenced(monkeypatch):
    monkeypatch.setenv("SF_PROGRESS", "0")
    out = io.StringIO()
    p = Progress(stream=out)
    p.stage("x")
    p.counter("y", 2).tick()
    assert out.getvalue() == ""


def test_long_loops_report_progress(capsys):
    fm, cache, dev, mem = build("mean_revert", seed=11, n_symbols=6)
    rows = [r for r in equity_rows(LadderConfig(max_positions=5, max_new_per_day=2)) if r.method == "rsi"]
    precompute(cache, rows, n_workers=1)
    run_catalog(fm, cache, dev, mem, rows)
    err = capsys.readouterr().err
    assert "[progress] trade cache (symbols) 6/6 (100.0%)" in err
    assert "[progress] catalogue rows 2/2 (100.0%) MR-RSI-SELL-EQ" in err
