from datetime import UTC, date, datetime

import pytest

from sfactory.scheduler.triggers import Trigger


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_daily_trigger_follows_new_york_daylight_saving_and_skips_weekends():
    t = Trigger("daily", time="16:30")
    assert t.next_fire(utc(2026, 7, 10, 12)) == utc(2026, 7, 10, 20, 30)       # EDT: 16:30 = 20:30 UTC
    assert t.next_fire(utc(2026, 1, 9, 12)) == utc(2026, 1, 9, 21, 30)         # EST: 16:30 = 21:30 UTC
    assert t.next_fire(utc(2026, 7, 10, 20, 30)) == utc(2026, 7, 13, 20, 30)   # strictly after; Friday -> Monday
    assert t.next_fire(utc(2026, 3, 6, 22)) == utc(2026, 3, 9, 20, 30)         # across the March DST switch


def test_bars_trigger_fires_after_every_bar_close_plus_the_delay():
    t = Trigger("bars", every_minutes=60, session_start="09:30", session_end="16:00", delay_minutes=5)
    fires = [f.strftime("%H:%M") for f in t.fires_on(date(2026, 7, 10))]
    assert fires == ["10:35", "11:35", "12:35", "13:35", "14:35", "15:35", "16:05"]   # last bar is 15:30-16:00
    assert t.next_fire(utc(2026, 7, 10, 20, 6)) == utc(2026, 7, 13, 14, 35)          # after Friday's last bar
    assert t.bar_label(utc(2026, 7, 10, 14, 35)).strftime("%H:%M") == "09:30"
    four = Trigger("bars", every_minutes=240, session_start="00:00", session_end="23:59", delay_minutes=2,
                   weekdays=(0, 1, 2, 3, 4, 6))
    assert [f.strftime("%H:%M") for f in four.fires_on(date(2026, 7, 12))][:2] == ["04:02", "08:02"]


@pytest.mark.parametrize("bad", [{"kind": "cron"}, {"tz": "Mars/Base"}, {"time": "25:00"}, {"weekdays": (7,)},
                                 {"kind": "bars", "session_start": "16:00", "session_end": "09:30"},
                                 {"delay_minutes": -1}])
def test_invalid_triggers_are_refused(bad):
    with pytest.raises(ValueError):
        Trigger(**bad)


# --- the service -------------------------------------------------------------------------------------------
class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _setup(tmp_path):
    pytest.importorskip("pydantic")                                    # the service needs the `web` group
    from sfactory.web.config_store import ConfigStore
    from sfactory.web.schemas import PlatformSettings
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "run_real.py").write_text("import sys; print('ARGS', sys.argv[1:])", encoding="utf-8")
    (scripts / "run_daily.py").write_text("import sys; sys.exit(3 if '--fail' in sys.argv else 0)", encoding="utf-8")
    (scripts / "bench_speed.py").write_text("import time; time.sleep(2.5)", encoding="utf-8")
    cfg = tmp_path / "cfg"
    store = ConfigStore(cfg)
    store.save_settings(PlatformSettings(scripts_dir=str(scripts)))
    store.put("job_presets", {"id": "ok", "name": "OK", "kind": "run_real", "args": {"day": "{today}"}}, True)
    store.put("job_presets", {"id": "bad", "name": "Bad", "kind": "run_daily", "args": {"fail": True}}, True)
    store.put("job_presets", {"id": "slow", "name": "Slow", "kind": "bench_speed", "args": {}}, True)
    return cfg, store


def _sched(store, sid, steps, create=True, **kw):
    store.put("schedules", {"id": sid, "name": sid, "steps": steps, **kw}, create)


def test_service_fires_chains_on_time_and_stops_at_the_first_failure(tmp_path):
    import json

    from sfactory.scheduler.service import SchedulerService
    from sfactory.web.jobs import JobManager
    cfg, store = _setup(tmp_path)
    _sched(store, "close", ["ok", "ok"])
    _sched(store, "broken", ["bad", "ok"])
    clock = Clock(utc(2026, 7, 10, 12, 0))
    svc = SchedulerService(cfg, clock=clock, poll=0.05)
    assert svc.tick() == []                                           # planned, nothing due yet
    clock.t = utc(2026, 7, 10, 20, 31)                                 # 16:31 New York
    runs = {r["schedule"]: r for r in svc.tick()}
    svc.join()
    done = {r["schedule"]: json.loads((cfg / "scheduler" / "runs" / f"{r['id']}.json").read_text())
            for r in runs.values()}
    assert done["close"]["status"] == "succeeded" and [s["status"] for s in done["close"]["steps"]] == ["succeeded"] * 2
    assert done["broken"]["status"] == "failed" and len(done["broken"]["steps"]) == 1       # stopped at step 1
    job = JobManager(cfg, tmp_path / "scripts").get(done["close"]["steps"][0]["job"])
    assert job["log"] == ["ARGS ['--day', '2026-07-10']"] and job["origin"] == "schedule:close"
    assert svc.state["close"]["next"] == "2026-07-13T20:30:00+00:00"   # next business day
    hb = json.loads((cfg / "scheduler" / "heartbeat.json").read_text())
    assert hb["schedules"] == 2


def test_missed_fires_are_recorded_or_run_once_and_edits_never_fire_immediately(tmp_path):
    from sfactory.scheduler.service import SchedulerService
    cfg, store = _setup(tmp_path)
    _sched(store, "live", ["ok"])                                      # misfire: skip (default)
    _sched(store, "paper", ["ok"], misfire="run_once")
    clock = Clock(utc(2026, 7, 10, 12, 0))
    svc = SchedulerService(cfg, clock=clock, poll=0.05)
    svc.tick()
    clock.t = utc(2026, 7, 10, 21, 10)                                 # 40 min after the fire: service was down
    ev = {r["schedule"]: r for r in svc.tick()}
    svc.join()
    assert ev["live"]["status"] == "missed" and ev["paper"]["note"] == "late run"
    _sched(store, "live", ["ok", "ok"], create=False)                  # edited: planned from now, not fired
    clock.t = utc(2026, 7, 13, 12, 0)
    assert svc.tick() == [] and svc.state["live"]["next"] == "2026-07-13T20:30:00+00:00"
    store.delete("schedules", "paper")
    svc.tick()
    assert "paper" not in svc.state


def test_a_run_still_going_is_not_started_again_and_the_dashboard_leaves_it_alone(tmp_path):
    from sfactory.scheduler.service import SchedulerService
    from sfactory.web.jobs import JobManager
    cfg, store = _setup(tmp_path)
    _sched(store, "bars", ["slow"], kind="bars", every_minutes=1, session_start="00:00", session_end="23:59",
           delay_minutes=0, weekdays=[0, 1, 2, 3, 4, 5, 6])
    clock = Clock(utc(2026, 7, 10, 14, 0, 30))
    svc = SchedulerService(cfg, clock=clock, poll=0.05)
    svc.tick()
    clock.t = utc(2026, 7, 10, 14, 1, 1)
    first = svc.tick()
    first_status = first[0]["status"]                                  # the thread updates the same record later
    import time
    time.sleep(0.5)
    web = JobManager(cfg, tmp_path / "scripts")                         # the dashboard's manager (runner "web")
    assert [j["status"] for j in web.list()] == ["running"]            # not marked "unknown"
    clock.t = utc(2026, 7, 10, 14, 2, 1)
    second = svc.tick()
    svc.join()
    assert first_status == "running" and second[0]["status"] == "skipped"
    assert [j["status"] for j in web.list()] == ["succeeded"]


def test_scheduler_api_and_single_instance(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from sfactory.scheduler.__main__ import main
    from sfactory.scheduler.service import SchedulerService
    from sfactory.web.app import create_app
    cfg, store = _setup(tmp_path)
    _sched(store, "close", ["ok"])
    c = TestClient(create_app(cfg, static_dir=tmp_path / "nodist", token=""))
    assert c.get("/api/scheduler").json()["alive"] is False
    SchedulerService(cfg).tick()                                        # real clock: heartbeat now
    s = c.get("/api/scheduler").json()
    assert s["alive"] and s["schedules"][0]["id"] == "close" and s["schedules"][0]["next"]
    with pytest.raises(SystemExit, match="already running"):
        main(["--config", str(cfg)])
    for kind in ("run_intraday", "check_survivorship"):                # every platform script can be a preset
        assert c.post("/api/config/job_presets", json={"id": kind.replace("_", "-"), "name": kind,
                                                        "kind": kind}).status_code == 201
    bad = {"id": "x", "name": "x", "steps": ["ok"], "kind": "bars", "session_start": "16:00", "session_end": "09:30"}
    assert c.post("/api/config/schedules", json=bad).status_code == 422
    assert c.post("/api/config/schedules", json={"id": "y", "name": "y", "steps": []}).status_code == 422
