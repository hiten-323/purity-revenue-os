"""Dial-guard regression tests (3 Nov 2026). Pure function tests: no DB, never dials."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ["AI_CALLING_ENABLED"] = "0"   # belt and braces: nothing here can dial
os.environ["AI_CALL_BUSINESS_HOURS"] = "1"

from app.services import founder_call_pipeline as p


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=p._IST()).astimezone(timezone.utc).replace(tzinfo=None)


def lead(window="", retry=0):
    return SimpleNamespace(id=1, founder_callback_window=window, callback_retry_count=retry)


def test_replay_lead_2302_stale_callback_is_refused_at_0642():
    l = lead("2026-11-02 10:30:00", retry=1)
    assert p.is_explicit_callback(l) is True          # the old bypass condition
    ok, why = p.call_hours_open(now=ist(2026, 11, 3, 6, 42), lead=l)
    assert ok is False, why
    assert "outside business hours" in why


def test_replay_lead_2302_sidecar_flag_is_false():
    l = lead("2026-11-02 10:30:00", retry=1)
    assert p.explicit_callback_due(l, now=ist(2026, 11, 3, 6, 42))[0] is False


def test_retry_flag_without_exact_time_does_not_lift_window():
    ok, _ = p.call_hours_open(now=ist(2026, 11, 3, 7, 0), lead=lead("", retry=1))
    assert ok is False


def test_due_7pm_callback_still_allowed_after_hours():
    l = lead("2026-11-04 19:00", retry=0)
    ok, why = p.call_hours_open(now=ist(2026, 11, 4, 19, 0), lead=l)
    assert ok is True and "explicit requested callback" in why


def test_five_minute_retry_still_allowed():
    l = lead("2026-11-04 19:00", retry=1)
    assert p.call_hours_open(now=ist(2026, 11, 4, 19, 0), lead=l)[0] is True


def test_callback_more_than_grace_past_is_refused_after_hours():
    l = lead("2026-11-04 19:00", retry=1)
    assert p.call_hours_open(now=ist(2026, 11, 4, 19, 31), lead=l)[0] is False


def test_callback_not_yet_due_after_hours_refused():
    l = lead("2026-11-04 20:30")
    assert p.call_hours_open(now=ist(2026, 11, 4, 19, 45), lead=l)[0] is False


def test_trai_floor_requested_10pm_refused():
    l = lead("2026-11-04 22:00")
    assert p.call_hours_open(now=ist(2026, 11, 4, 22, 0), lead=l)[0] is False


def test_trai_floor_requested_8am_refused():
    l = lead("2026-11-04 08:00")
    assert p.call_hours_open(now=ist(2026, 11, 4, 8, 0), lead=l)[0] is False


def test_stale_callback_inside_hours_still_uses_normal_window():
    l = lead("2026-11-02 10:30:00", retry=1)
    assert p.call_hours_open(now=ist(2026, 11, 3, 10, 0), lead=l)[0] is True


def test_normal_window_unchanged():
    assert p.call_hours_open(now=ist(2026, 11, 3, 9, 29))[0] is False
    assert p.call_hours_open(now=ist(2026, 11, 3, 9, 30))[0] is True
    assert p.call_hours_open(now=ist(2026, 11, 3, 19, 0))[0] is True
    assert p.call_hours_open(now=ist(2026, 11, 3, 19, 1))[0] is False
    assert p.call_hours_open(now=ist(2026, 11, 8, 12, 0))[0] is False   # Sunday


def test_garbage_lead_fails_closed():
    class Boom:
        id = 9
        callback_retry_count = 1
        @property
        def founder_callback_window(self):
            raise RuntimeError("x")
    assert p.explicit_callback_due(Boom(), now=ist(2026, 11, 3, 6, 42))[0] is False
