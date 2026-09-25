import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sfactory.forward import alerts, notify


class FakeTelegram:
    """A local stand-in for api.telegram.org (and for an HTTP proxy in front of it)."""

    def __init__(self):
        self.received, self.ok, self.paths = [], True, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.paths.append(self.path)
                outer.received.append(body)
                out = json.dumps({"ok": outer.ok, "description": "chat not found"} if not outer.ok else {"ok": True})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out.encode())

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def tg():
    t = FakeTelegram()
    yield t
    t.close()


def _notifier(tmp_path, url, proxy="", level="warning"):
    return notify.Notifier(tmp_path, notify.TelegramChannel("TOKEN", "42", proxy=proxy, api=url), level)


def test_new_events_are_sent_once_at_or_above_the_level(tmp_path, tg):
    ev = tmp_path / "events.jsonl"
    alerts.emit(ev, "critical", "old", "before the notifier existed")
    n = _notifier(tmp_path, tg.url)
    assert n.flush() == {"sent": 0} and tg.received == []              # old events are not replayed
    alerts.emit(ev, "warning", "scheduler", "run missed")
    alerts.emit(ev, "info", "dashboard", "resumed")
    alerts.emit(ev, "critical", "run_daily", "kill switch tripped: drawdown")
    assert n.flush() == {"sent": 2}
    assert [m["chat_id"] for m in tg.received] == ["42", "42"] and tg.paths[0] == "/botTOKEN/sendMessage"
    assert "run missed" in tg.received[0]["text"] and "CRITICAL" in tg.received[1]["text"]
    assert n.flush() == {"sent": 0} and len(tg.received) == 2          # nothing twice


def test_failures_keep_the_events_for_the_next_pass(tmp_path, tg):
    ev = tmp_path / "events.jsonl"
    alerts.emit(ev, "info", "x", "start")
    down = _notifier(tmp_path, "http://127.0.0.1:9")                   # nothing listens there
    down.flush()
    alerts.emit(ev, "critical", "run_daily", "tripped")
    r = down.flush()
    assert r["sent"] == 0 and "error" in r and notify.status(tmp_path)["last_error"]
    tg.ok = False                                                       # Telegram answers ok: false
    assert "refused" in _notifier(tmp_path, tg.url).flush()["error"]
    tg.ok = True
    assert _notifier(tmp_path, tg.url).flush() == {"sent": 1}          # the same event, delivered now
    assert notify.status(tmp_path)["last_error"] is None


def test_a_long_outage_ends_in_a_summary_not_a_flood(tmp_path, tg):
    ev = tmp_path / "events.jsonl"
    n = _notifier(tmp_path, tg.url)
    n.flush()
    for i in range(35):
        alerts.emit(ev, "warning", "scheduler", f"missed {i}")
    assert n.flush() == {"sent": 35}
    assert len(tg.received) == notify.MAX_PER_PASS + 1 and "15 more" in tg.received[-1]["text"]


def test_messages_go_through_an_http_proxy(tmp_path, tg):
    ev = tmp_path / "events.jsonl"
    n = _notifier(tmp_path, "http://api.telegram.invalid", proxy=tg.url)   # only reachable via the proxy
    n.flush()
    alerts.emit(ev, "critical", "run_daily", "via proxy")
    assert n.flush() == {"sent": 1}
    assert tg.paths[-1] == "http://api.telegram.invalid/botTOKEN/sendMessage"   # absolute URI: a proxy request


def test_the_scheduler_sends_and_the_dashboard_tests_the_channel(tmp_path, tg, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from sfactory.scheduler.service import SchedulerService
    from sfactory.web.app import create_app
    from sfactory.web.config_store import ConfigStore
    from sfactory.web.schemas import PlatformSettings
    cfg = tmp_path / "cfg"
    c = TestClient(create_app(cfg, static_dir=tmp_path / "nodist", token=""))
    monkeypatch.delenv("SF_TELEGRAM_TOKEN", raising=False)
    assert c.post("/api/notify/test").status_code == 422                # not configured
    ConfigStore(cfg).save_settings(PlatformSettings(telegram_chat_id="42", telegram_api=tg.url))
    monkeypatch.setenv("SF_TELEGRAM_TOKEN", "TOKEN")
    assert c.post("/api/notify/test").json() == {"sent": True} and "test message" in tg.received[-1]["text"]
    svc = SchedulerService(cfg)
    svc.tick()                                                          # first pass: starts at the end of the log
    c.post("/api/killswitch/trip", json={"mode": "halt_new", "reason": "broker outage"})
    svc.tick()
    assert "broker outage" in tg.received[-1]["text"]
    st = c.get("/api/notify").json()
    assert st["token_set"] and st["chat_set"] and st["sent_total"] == 1
