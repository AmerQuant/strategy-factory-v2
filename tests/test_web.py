import json
import time
from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from sfactory.web.app import create_app
from sfactory.web.jobs import build_args

ROW = {"method": "rsi", "direction": 1, "rung": "A1", "max_positions": 10}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "cfg", static_dir=tmp_path / "nodist", token="")), tmp_path


def test_health_meta_and_settings(client):
    c, tmp = client
    assert c.get("/api/health").json()["ok"]
    meta = c.get("/api/meta").json()
    rsi = next(m for m in meta["methods"] if m["method"] == "rsi")
    assert rsi["family"] == "MR" and rsi["grid"] and rsi["neutral_exit"]["kind"] == "prev_high"
    assert "two_clock" in meta["activation_modes"] and "catalogues" in meta["collections"]
    s = c.get("/api/settings").json()
    s["runs_root"] = str(tmp / "runs")
    assert c.put("/api/settings", json=s).json()["runs_root"] == str(tmp / "runs")
    assert c.get("/api/settings").json()["runs_root"] == str(tmp / "runs")


def test_documents_are_validated_against_the_platform(client):
    c, _ = client
    doc = {"id": "eq-core", "name": "Equity core", "rows": [ROW, {**ROW, "method": "donchian_break", "direction": -1}]}
    assert c.post("/api/config/catalogues", json=doc).status_code == 201
    assert c.post("/api/config/catalogues", json=doc).status_code == 409
    bad = {**doc, "id": "bad", "rows": [{**ROW, "not_a_field": 1}]}
    assert c.post("/api/config/catalogues", json=bad).status_code == 422
    assert c.post("/api/config/catalogues", json={**doc, "id": "Bad Id"}).status_code == 422
    pol = {"id": "p1", "name": "P", "entries": [{"config": ROW, "activation": {"mode": "two_clock"}}]}
    assert c.post("/api/config/policies", json=pol).status_code == 201
    badact = {"id": "p2", "name": "P", "entries": [{"config": ROW, "activation": {"mode": "x", "nope": 1}}]}
    assert c.post("/api/config/policies", json=badact).status_code == 422
    ov = {"id": "p3", "name": "P", "entries": [{"config": ROW, "overlay": {"dd_limit": 0.15, "cut": 0.5}}]}
    assert c.post("/api/config/policies", json=ov).status_code == 201
    badov = {"id": "p4", "name": "P", "entries": [{"config": ROW, "overlay": {"nope": 1}}]}
    assert c.post("/api/config/policies", json=badov).status_code == 422
    rb = {"id": "rb", "name": "Budget", "max_weight": 0.3, "family_cap": 0.6, "target_vol_ann": 0.1}
    assert c.post("/api/config/risk_budgets", json=rb).status_code == 201
    assert c.post("/api/config/risk_budgets", json={**rb, "id": "rb2", "max_weight": 3}).status_code == 422
    doc["description"] = "edited"
    assert c.put("/api/config/catalogues/eq-core", json=doc).json()["description"] == "edited"
    assert [d["id"] for d in c.get("/api/config/catalogues").json()] == ["eq-core"]
    assert c.delete("/api/config/catalogues/eq-core").status_code == 204
    assert c.get("/api/config/catalogues/eq-core").status_code == 404
    assert c.get("/api/config/nope").status_code == 404


def _fake_run(root, name):
    d = root / name
    d.mkdir(parents=True)
    pkg = {"generated_at": "2026-09-25T10:00:00+00:00", "meta": {"data": "v1store-1D-x", "catalog": "2026-10-v1",
                                                                  "timeframe": "1D"},
           "trials_in_registry": 40, "combined_dev": {"sharpe": 1.2, "effective_n": 3.1},
           "rows": [{"row": "MR-RSI-BUY-EQ", "path": "standalone", "sharpe": 1.1, "fold_decisions": [{"dp": "x"}]},
                    {"row": "TF-MA_CROSS-BUY-EQ", "path": None, "sharpe": 0.2, "fold_decisions": None}],
           "dev_curve": {"dates": ["2020-01-03"], "combined": [1.0], "benchmark": [0.5]},
           "holdout": {"status": "passed", "policy": {"rows": [ROW]}}}
    (d / "evidence.json").write_text(json.dumps(pkg), encoding="utf-8")
    (d / "report.html").write_text("<html>report</html>", encoding="utf-8")


def test_runs_registry_and_policy_from_a_run(client):
    c, tmp = client
    runs = tmp / "runs"
    _fake_run(runs, "run1")
    from sfactory.registry.repo import Registry
    reg = Registry(str(tmp / "reg.duckdb"))
    reg.record_trial("MR-RSI-BUY-EQ", {"a": 1}, "v1", "0.6.0", {"sharpe": 1.0})
    reg.con.close()
    s = c.get("/api/settings").json()
    c.put("/api/settings", json={**s, "runs_root": str(runs), "registry_path": str(tmp / "reg.duckdb")})
    lst = c.get("/api/runs").json()
    assert lst[0]["id"] == "run1" and lst[0]["accepted"] == 1 and lst[0]["holdout"] == "passed"
    det = c.get("/api/runs/run1").json()
    assert "fold_decisions" not in det["rows"][0] and det["has_report"]
    assert c.get("/api/runs/run1/rows/MR-RSI-BUY-EQ").json()["fold_decisions"] == [{"dp": "x"}]
    assert "report" in c.get("/api/runs/run1/report").text
    p = c.post("/api/runs/run1/policy", json={}).json()
    assert p["source_run"] == "run1" and p["entries"][0]["config"]["method"] == "rsi"
    r = c.get("/api/registry").json()
    assert r["available"] and r["trials"] == 1 and r["recent"][0]["metrics"]["sharpe"] == 1.0


def test_live_state_views(client):
    c, tmp = client
    from sfactory.broker.sim import SimulatedBroker
    from sfactory.data.adjust import add_adj_factor
    from sfactory.data.synthetic import make_market
    from sfactory.engine.cache import prepare_arrays
    from sfactory.forward.daily import daily_step, new_state
    from sfactory.policy.ladder import LadderConfig
    bars, divs, mem = make_market(5, 1000, seed=3, kind="mean_revert", dividends=False, listings=False)
    bars = add_adj_factor(bars, divs)
    arrays = prepare_arrays(bars, divs)
    st = new_state([LadderConfig(rung="A1", method="rsi", min_price=0, min_dollar_vol=0, min_is_trades=5)],
                   first_dp=date(2013, 1, 1), data_start=date(2010, 1, 4))
    br = SimulatedBroker()
    for d in arrays["S000"].dates[(arrays["S000"].dates >= date(2013, 1, 2))][:60]:
        daily_step(st, arrays, bars, mem, d.astype(object), br)
    live = tmp / "live"
    live.mkdir()
    st.save(live / "paper1.json")
    s = c.get("/api/settings").json()
    c.put("/api/settings", json={**s, "live_dir": str(live)})
    lst = c.get("/api/live").json()
    assert lst[0]["id"] == "paper1" and lst[0]["rows"] == 1
    det = c.get("/api/live/paper1").json()
    assert det["book"][0]["method"] == "rsi" and len(det["equity"]) == len(det["closed"]) > 0


def test_jobs_run_the_platform_scripts(client, tmp_path):
    c, tmp = client
    assert build_args({"store": "D:/x", "diverse": True, "off": False, "workers": 0, "universe": ["a", "b"]}) == [
        "--store", "D:/x", "--diverse", "--workers", "0", "--universe", "a", "--universe", "b"]
    scripts = tmp / "scripts"
    scripts.mkdir()
    (scripts / "run_real.py").write_text("import sys; print('ARGS', sys.argv[1:])", encoding="utf-8")
    s = c.get("/api/settings").json()
    c.put("/api/settings", json={**s, "scripts_dir": str(scripts)})
    c.post("/api/config/job_presets", json={"id": "real", "name": "Real", "kind": "run_real",
                                            "args": {"store": "S", "diverse": True}})
    j = c.post("/api/jobs", json={"preset": "real", "args": {"workers": 2}}).json()
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running":
            break
        time.sleep(0.05)
    assert info["status"] == "succeeded" and "--diverse" in info["log"][0] and "--workers" in info["log"][0]
    assert c.get("/api/jobs").json()[0]["id"] == j["id"]
    assert c.post("/api/jobs", json={"kind": "nope"}).status_code == 422


def test_jobs_can_import_the_package_from_a_bare_interpreter(client):
    c, tmp = client
    root = tmp / "proj"
    (root / "scripts").mkdir(parents=True)
    (root / "src" / "mypkg").mkdir(parents=True)
    (root / "src" / "mypkg" / "__init__.py").write_text("VALUE = 42", encoding="utf-8")
    (root / "scripts" / "run_real.py").write_text("import mypkg; print('VALUE', mypkg.VALUE)", encoding="utf-8")
    s = c.get("/api/settings").json()
    c.put("/api/settings", json={**s, "scripts_dir": str(root / "scripts")})
    j = c.post("/api/jobs", json={"kind": "run_real", "args": {}}).json()
    for _ in range(100):
        info = c.get(f"/api/jobs/{j['id']}").json()
        if info["status"] != "running":
            break
        time.sleep(0.05)
    assert info["status"] == "succeeded" and info["log"] == ["VALUE 42"]


def test_token_authentication(tmp_path):
    tok = "correct-horse-battery-staple"
    c = TestClient(create_app(tmp_path / "cfg", static_dir=tmp_path / "nodist", token=tok))
    h = c.get("/api/health").json()
    assert h["auth_required"] and not h["authenticated"] and "config_dir" not in h
    assert c.get("/api/settings").status_code == 401 and c.get("/api/config/catalogues").status_code == 401
    assert c.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert c.get("/api/settings", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    r = c.post("/api/login", json={"token": tok})
    assert r.status_code == 200 and "sf_session" in r.cookies and tok not in r.headers.get("set-cookie", "")
    assert c.get("/api/settings").status_code == 200 and c.get("/api/health").json()["authenticated"]
    c.post("/api/logout")
    c.cookies.clear()
    assert c.get("/api/settings").status_code == 401


def test_server_refuses_network_bind_without_token(monkeypatch, tmp_path):
    from sfactory.web.__main__ import main
    monkeypatch.delenv("SF_WEB_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="refusing"):
        main(["--config", str(tmp_path), "--host", "0.0.0.0"])
    (tmp_path / "t.txt").write_text("short", encoding="utf-8")
    with pytest.raises(SystemExit, match="16 characters"):
        main(["--config", str(tmp_path), "--host", "0.0.0.0", "--token-file", str(tmp_path / "t.txt")])
