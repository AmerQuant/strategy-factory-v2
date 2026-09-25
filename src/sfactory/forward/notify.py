"""Telegram notifications for the event log (docs/spec/killswitch.md, "Notifications").

The scheduler service calls `Notifier.flush()` every tick: new lines of `<config>/events.jsonl` at or above
`min_level` are sent to one Telegram chat; the byte offset reached is kept in `<config>/notify_state.json`, so every
event is sent once and nothing is lost when Telegram is unreachable (the next pass retries from the same place).

Settings (admin Platform page): `telegram_chat_id`, `telegram_min_level` (critical | warning | info),
`telegram_proxy` (an HTTP proxy such as http://127.0.0.1:10809 - Telegram is not reachable from every network;
SOCKS proxies need a local HTTP bridge, the standard library speaks HTTP proxies only).
The bot token is never stored: it is read from the SF_TELEGRAM_TOKEN environment variable.

    uv run python -m sfactory.forward.notify --config D:/sf2_admin --test      # sends a test message
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

LEVELS = {"info": 0, "warning": 1, "critical": 2}
ICON = {"critical": "\U0001F6A8", "warning": "\u26A0\uFE0F", "info": "\u2139\uFE0F"}
MAX_PER_PASS = 20                                   # a long outage ends in one summary, not a flood


class TelegramChannel:
    def __init__(self, token: str, chat_id: str, proxy: str = "", api: str = "https://api.telegram.org",
                 timeout: float = 10.0):
        self.token, self.chat_id, self.api, self.timeout = token, str(chat_id), api.rstrip("/"), timeout
        handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
        self.opener = urllib.request.build_opener(*handlers)

    def send(self, text: str) -> None:
        body = json.dumps({"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"{self.api}/bot{self.token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=self.timeout) as r:
            reply = json.loads(r.read().decode() or "{}")
        if not reply.get("ok"):
            raise RuntimeError(f"Telegram refused the message: {reply.get('description', reply)}")


def format_event(e: dict) -> str:
    lvl = e.get("level", "info")
    return f"{ICON.get(lvl, '')} {lvl.upper()} \u00b7 {e.get('source', '')}\n{e.get('message', '')}\n{e.get('at', '')}"


class Notifier:
    def __init__(self, config_dir: str | Path, channel, min_level: str = "warning"):
        self.root = Path(config_dir)
        self.events, self.state_file = self.root / "events.jsonl", self.root / "notify_state.json"
        self.channel, self.min_level = channel, LEVELS.get(min_level, 1)

    def _state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"offset": None}

    def _save(self, st: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(self.state_file)

    def flush(self) -> dict:
        """Send what is new. The first pass starts at the end of the log (old events are not replayed)."""
        st = self._state()
        size = self.events.stat().st_size if self.events.exists() else 0
        if st.get("offset") is None or st["offset"] > size:        # first run, or the log was rotated
            st["offset"] = size
            self._save(st)
            return {"sent": 0}
        with self.events.open("rb") as fh:
            fh.seek(st["offset"])
            chunk = fh.read()
        end = chunk.rfind(b"\n") + 1                                # only complete lines
        lines = [ln for ln in chunk[:end].decode("utf-8", errors="replace").splitlines() if ln.strip()]
        evs = []
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if LEVELS.get(e.get("level"), 0) >= self.min_level:
                evs.append(e)
        try:
            for e in evs[:MAX_PER_PASS]:
                self.channel.send(format_event(e))
            if len(evs) > MAX_PER_PASS:
                self.channel.send(f"\u2026 and {len(evs) - MAX_PER_PASS} more events: see the dashboard (Paper & live).")
        except (OSError, urllib.error.URLError, RuntimeError, ValueError) as err:
            st.update(last_error=f"{type(err).__name__}: {err}", last_error_at=_now())
            self._save(st)                                           # offset unchanged: retried next pass
            return {"sent": 0, "error": st["last_error"]}
        st["offset"] += end
        if evs:
            st.update(last_sent_at=_now(), sent_total=st.get("sent_total", 0) + len(evs), last_error=None)
        self._save(st)
        return {"sent": len(evs)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def from_settings(config_dir: str | Path, settings, token: str | None = None) -> Notifier | None:
    """The configured notifier, or None when no chat id or no SF_TELEGRAM_TOKEN is set."""
    token = token if token is not None else os.environ.get("SF_TELEGRAM_TOKEN", "")
    chat = getattr(settings, "telegram_chat_id", "")
    if not (token and chat):
        return None
    ch = TelegramChannel(token, chat, getattr(settings, "telegram_proxy", ""),
                         getattr(settings, "telegram_api", "") or "https://api.telegram.org")
    return Notifier(config_dir, ch, getattr(settings, "telegram_min_level", "warning"))


def status(config_dir: str | Path) -> dict:
    p = Path(config_dir) / "notify_state.json"
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    return {"token_set": bool(os.environ.get("SF_TELEGRAM_TOKEN")), **{k: st.get(k) for k in
            ("last_sent_at", "sent_total", "last_error", "last_error_at")}}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--test", action="store_true", help="send a test message")
    a = ap.parse_args(argv)
    from sfactory.web.config_store import ConfigStore
    n = from_settings(a.config, ConfigStore(a.config).settings())
    if n is None:
        raise SystemExit("set telegram_chat_id in the Platform settings and SF_TELEGRAM_TOKEN in the environment")
    if a.test:
        n.channel.send("\u2705 Strategy Factory: test message")
        print("sent")
    else:
        print(n.flush())


if __name__ == "__main__":
    main()
