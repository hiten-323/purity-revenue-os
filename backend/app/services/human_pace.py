"""Human-like pacing for automatic outreach (Hiten's no-spam rule, 25 Oct 2026).

Env-gated, conservative defaults. Non-blocking: callers ask may_send_email()
/ may_dial(); if False they skip (lead stays queued for a later cycle).
Business-hours gating stays where it already is (email_sender / calling gates).

  HUMAN_PACE_ENABLED          1   master switch (0 = old behaviour)
  EMAIL_GAP_MIN_SECONDS     180   random gap between automatic emails
  EMAIL_GAP_MAX_SECONDS     720
  EMAIL_HOURLY_CAP_MIN        4   per-clock-hour cap, randomised in [min,max]
  EMAIL_HOURLY_CAP_MAX        6
  AI_CALL_GAP_MIN_SECONDS   180   random gap between AI dial attempts
  AI_CALL_GAP_MAX_SECONDS   480
"""
from __future__ import annotations

import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_lock = threading.Lock()
_state = {
    "email_last": None, "email_gap": 0.0,
    "email_hour": None, "email_hour_count": 0, "email_hour_cap": None,
    "call_next_at": 0.0,
}


def _on(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except ValueError:
        return float(default)


def enabled() -> bool:
    return _on("HUMAN_PACE_ENABLED", "1")


def _rand(lo_name, lo, hi_name, hi) -> float:
    a, b = _num(lo_name, lo), _num(hi_name, hi)
    if b < a:
        a, b = b, a
    return random.uniform(a, b)


def may_send_email(now: float | None = None) -> tuple[bool, str]:
    if not enabled():
        return True, "human_pace off"
    now = time.time() if now is None else now
    hour = datetime.fromtimestamp(now, IST).strftime("%Y%m%d%H")
    with _lock:
        if _state["email_hour"] != hour:
            lo = int(_num("EMAIL_HOURLY_CAP_MIN", 4)); hi = int(_num("EMAIL_HOURLY_CAP_MAX", 6))
            _state.update(email_hour=hour, email_hour_count=0,
                          email_hour_cap=random.randint(min(lo, hi), max(lo, hi)))
        if _state["email_hour_count"] >= _state["email_hour_cap"]:
            return False, f"HELD: human_pace hourly cap {_state['email_hour_cap']} reached"
        last = _state["email_last"]
        if last is not None and now - last < _state["email_gap"]:
            return False, f"HELD: human_pace gap {int(_state['email_gap'] - (now - last))}s left"
    return True, "ok"


def record_email_sent(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _lock:
        _state["email_last"] = now
        _state["email_hour_count"] += 1
        _state["email_gap"] = _rand("EMAIL_GAP_MIN_SECONDS", 180, "EMAIL_GAP_MAX_SECONDS", 720)


def may_dial(now: float | None = None) -> bool:
    if not enabled():
        return True
    now = time.monotonic() if now is None else now
    with _lock:
        return now >= _state["call_next_at"]


def record_dial_attempt(dialed: bool, now: float | None = None) -> None:
    """After a cycle that dialed, wait a fresh random gap before the next one."""
    if not dialed:
        return
    now = time.monotonic() if now is None else now
    with _lock:
        _state["call_next_at"] = now + _rand("AI_CALL_GAP_MIN_SECONDS", 180, "AI_CALL_GAP_MAX_SECONDS", 480)


def call_tick_seconds(default_tick: int) -> float:
    """Random wake-up interval for the AI call tick loop (replaces fixed 120s)."""
    if not enabled():
        return float(default_tick)
    return _rand("AI_CALL_GAP_MIN_SECONDS", 180, "AI_CALL_GAP_MAX_SECONDS", 480)


# ── Daily cap (Hiten 26 Oct 2026: server gets 15/day of the shared 40 connect@ budget) ──
#   EMAIL_DAILY_CAP            15   prospect emails per IST day (hard max 15; 0/blank/>15 -> 15, 4 Nov)
#                                   (connect@ sends 40/day in total; box bots 25, this server 15)
#   EMAIL_DAILY_STATE_FILE     /opt/purity-revenue-os/email_daily_count.json (survives restarts)
import json as _json

LIVE_DAILY_FILE = "/opt/purity-revenue-os/email_daily_count.json"


def _under_pytest() -> bool:
    import sys as _sys
    return "pytest" in _sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def _daily_file() -> str:
    path = os.getenv("EMAIL_DAILY_STATE_FILE", LIVE_DAILY_FILE)
    # 3 Nov 2026: test runs were bumping the LIVE counter (26 real sends read
    # as 30). Under pytest the live file is never used, even if a test forgot
    # to point EMAIL_DAILY_STATE_FILE at a temp path.
    if _under_pytest() and os.path.realpath(path) == os.path.realpath(LIVE_DAILY_FILE):
        import tempfile as _tf
        path = os.path.join(_tf.gettempdir(), f"pytest_email_daily_count_{os.getpid()}.json")
    return path


# ── Email-only on/off switch (3 Nov 2026, Hiten "you judge" via B-B OUTREACH) ──
#   EMAIL_SENDING_ENABLED      1   0/off/false/no (or any unrecognised value) holds
#                                  every prospect email; calls and WhatsApp unaffected.
_EMAIL_ON = {"1", "true", "yes", "on"}


def email_sending_enabled() -> tuple[bool, str]:
    # Fail closed: a missing or blank flag is NOT permission to send.
    raw = os.getenv("EMAIL_SENDING_ENABLED", "0")
    if raw is not None and raw.strip().lower() in _EMAIL_ON:
        return True, "ok"
    shown = (raw or "").strip()[:12] or "unset"
    return False, f"email sending switched off (EMAIL_SENDING_ENABLED={shown})"

def _today(now=None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, IST).strftime("%Y-%m-%d")

# 4 Nov 2026 (Hiten "stop,fix all,resume"; audit found 26 sent on 24 Oct and
# 24 on 3 Nov against a cap of 15): the cap is HARD. It can never be switched off
# or raised above 15 by env, and the count is the larger of the JSON counter and
# today's (IST) SENT rows in email_drafts, so a lost/reset counter cannot let
# extra mail out.
EMAIL_DAILY_HARD_MAX = 15
LIVE_DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "purity_beans.db")


def effective_daily_cap() -> int:
    try:
        cap = int(_num("EMAIL_DAILY_CAP", EMAIL_DAILY_HARD_MAX))
    except Exception:
        cap = EMAIL_DAILY_HARD_MAX
    if cap <= 0 or cap > EMAIL_DAILY_HARD_MAX:
        return EMAIL_DAILY_HARD_MAX
    return cap


def db_sent_today(now=None) -> int:
    """SENT email_drafts whose sent_at (stored UTC) falls on today's IST date."""
    path = os.getenv("EMAIL_DAILY_DB_FILE") or ("" if _under_pytest() else LIVE_DB_FILE)
    if not path or not os.path.exists(path):
        return 0
    try:
        import sqlite3 as _sq
        con = _sq.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM email_drafts WHERE status='SENT' AND sent_at IS NOT NULL "
                "AND substr(datetime(sent_at, '+330 minutes'), 1, 10) = ?", (_today(now),)).fetchone()
        finally:
            con.close()
        return int(row[0] or 0)
    except Exception:
        return 0


def _file_count(now=None) -> int:
    try:
        d = _json.load(open(_daily_file()))
        return int(d.get("count", 0)) if d.get("day") == _today(now) else 0
    except Exception:
        return 0


# 4 Nov 2026 (Hiten 15:23 IST, follow-up 2): ONE 15/day cap shared by the
# server and the box send gate (/workspace/crm/send_gate.py). The box appends
# each of its sends to BOX_LEDGER (scripts/email_cap_record_box.py over ssh) and
# reads server_daily_count() (scripts/email_cap_status.py) before every send.
LIVE_BOX_LEDGER = os.path.join(os.path.dirname(LIVE_DB_FILE), "data", "box_email_sends.jsonl")


def _box_ledger() -> str:
    return os.getenv("EMAIL_BOX_LEDGER_FILE") or ("" if _under_pytest() else LIVE_BOX_LEDGER)


def box_sent_today(now=None) -> int:
    """Emails the box send gate logged today (IST); each line is one send."""
    path = _box_ledger()
    if not path or not os.path.exists(path):
        return 0
    day, ids = _today(now), set()
    try:
        for line in open(path, encoding="utf-8"):
            try:
                r = _json.loads(line)
            except Exception:
                continue
            if r.get("day") == day:
                ids.add(r.get("id") or line)
    except Exception:
        return 0
    return len(ids)


def server_daily_count(now=None) -> int:
    """This server's own sends today (what the box adds to its ledger count)."""
    return max(_file_count(now), db_sent_today(now))


def daily_count(now=None) -> int:
    return server_daily_count(now) + box_sent_today(now)

def daily_cap_ok(now=None) -> tuple[bool, str]:
    cap = effective_daily_cap()
    n = daily_count(now)
    if n >= cap:
        return False, f"HELD: daily email cap {cap} reached ({n} today IST)"
    return True, "ok"

def record_daily_sent(now=None) -> None:
    with _lock:
        n = _file_count(now) + 1
        tmp = _daily_file() + ".tmp"
        with open(tmp, "w") as fh:
            _json.dump({"day": _today(now), "count": n}, fh)
        os.replace(tmp, _daily_file())
