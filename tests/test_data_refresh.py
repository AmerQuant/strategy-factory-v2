import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

FAKE_SFAC = """import os, sys
args = sys.argv[1:]
print("SFAC", " ".join(args), os.environ.get("SFAC_DATA_ROOT", "-"))
sys.exit(4 if "fail" in args else 0)
"""


def _repo(tmp_path):
    repo = tmp_path / "v1"
    repo.mkdir()
    (repo / "fake_sfac.py").write_text(FAKE_SFAC, encoding="utf-8")
    return repo, f'"{sys.executable}" fake_sfac.py'


def test_refresh_runs_v1_steps_in_order_and_stops_at_the_first_failure(tmp_path):
    import refresh_v1_data
    repo, runner = _repo(tmp_path)
    ok = refresh_v1_data.main(["--v1-repo", str(repo), "--runner", runner, "--data-root", "D:/store",
                               "--step", "data download dukascopy --series h1",
                               "--steps", "data ingest dukascopy --series h1 --set-reference"])
    assert ok["ok"] and [s["step"] for s in ok["steps"]] == ["data download dukascopy --series h1",
                                                             "data ingest dukascopy --series h1 --set-reference"]
    assert ok["steps"][0]["tail"] == ["SFAC data download dukascopy --series h1 D:/store"]   # cwd, env, args
    with pytest.raises(SystemExit) as e:
        refresh_v1_data.main(["--v1-repo", str(repo), "--runner", runner, "--steps", "data fail; data never"])
    assert e.value.code == 1
    rep = refresh_v1_data.run_steps(repo, ["data fail", "data never"], runner)
    assert not rep["ok"] and rep["failed_step"] == "data fail" and len(rep["steps"]) == 1
    with pytest.raises(SystemExit, match="not found"):
        refresh_v1_data.main(["--v1-repo", str(tmp_path / "nope"), "--step", "x"])
    with pytest.raises(SystemExit, match="at least one"):
        refresh_v1_data.main(["--v1-repo", str(repo)])


def test_new_scripts_are_job_presets(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from sfactory.web.app import create_app
    from sfactory.web.jobs import SCRIPTS
    root = Path(__file__).resolve().parents[1]
    for kind, script in SCRIPTS.items():
        assert (root / "scripts" / script).is_file(), kind              # every job kind has its script
    c = TestClient(create_app(tmp_path / "cfg", static_dir=tmp_path / "nodist", token=""))
    for kind in ("export_mt5_specs", "convert_mt5_costs", "refresh_v1_data"):
        doc = {"id": kind.replace("_", "-"), "name": kind, "kind": kind, "args": {}}
        assert c.post("/api/config/job_presets", json=doc).status_code == 201
    repo, runner = _repo(tmp_path)
    s = c.get("/api/settings").json()
    c.put("/api/settings", json={**s, "scripts_dir": str(root / "scripts")})
    c.put("/api/config/job_presets/refresh-v1-data", json={"id": "refresh-v1-data", "name": "refresh",
                                                           "kind": "refresh_v1_data", "args": {
                                                               "v1-repo": str(repo), "runner": runner,
                                                               "steps": "data download dukascopy --series h1"}})
    j = c.post("/api/jobs", json={"preset": "refresh-v1-data"}).json()
    import time
    for _ in range(200):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running":
            break
        time.sleep(0.05)
    assert info["status"] == "succeeded", info["log"]
    assert json.loads("\n".join(info["log"][1:]))["ok"]
