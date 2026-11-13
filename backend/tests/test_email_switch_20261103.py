"""3 Nov 2026: EMAIL_SENDING_ENABLED switch + tests never touch the live counter."""
import json
import os

import pytest

from app.services import human_pace as hp

LIVE = "/opt/purity-revenue-os/email_daily_count.json"


def _live_snapshot():
    try:
        with open(LIVE, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def test_conftest_points_counter_at_temp_path():
    path = os.environ["EMAIL_DAILY_STATE_FILE"]
    assert os.path.realpath(path) != os.path.realpath(LIVE)
    assert hp._daily_file() == path


def test_record_daily_sent_never_writes_live_counter(monkeypatch):
    before = _live_snapshot()
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", LIVE)  # simulate a careless test
    assert os.path.realpath(hp._daily_file()) != os.path.realpath(LIVE)
    hp.record_daily_sent()
    hp.record_daily_sent()
    assert _live_snapshot() == before
    assert hp.daily_count() == 2  # counted in the redirected temp file


def test_counter_isolated_per_test_starts_at_zero():
    assert hp.daily_count() == 0


@pytest.mark.parametrize("val", [None, ""])
def test_missing_or_blank_switch_defaults_off(monkeypatch, val):
    if val is None:
        monkeypatch.delenv("EMAIL_SENDING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("EMAIL_SENDING_ENABLED", val)
    ok, why = hp.email_sending_enabled()
    assert ok is False and "EMAIL_SENDING_ENABLED" in why


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_switch_on_values(monkeypatch, val):
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", val)
    assert hp.email_sending_enabled()[0] is True


@pytest.mark.parametrize("val", ["0", "off", "false", "no", "garbage"])
def test_switch_off_values_fail_closed(monkeypatch, val):
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", val)
    ok, why = hp.email_sending_enabled()
    assert ok is False and "EMAIL_SENDING_ENABLED" in why


def _email(to="buyer@example-cafe.in"):
    from app.services.email_sender import OutreachEmail
    return OutreachEmail(to_email=to, to_name="Buyer", company="Cafe",
                         subject="Pure instant coffee", body_text="Hello", lead_id=None)


def test_send_email_held_when_switch_off(monkeypatch):
    import smtplib
    from app.services import email_sender

    def _boom(*a, **k):
        raise AssertionError("SMTP must not be opened when email is switched off")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", "0")
    out = email_sender.send_email(_email())
    assert out.status == "failed"
    assert out.error.startswith("HELD:") and "EMAIL_SENDING_ENABLED" in out.error
    assert hp.daily_count() == 0


def test_switch_on_does_not_skip_other_gates(monkeypatch):
    """Switch on only removes this one hold; the next gates still run."""
    import smtplib
    from app.services import email_sender

    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no SMTP")))
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", "1")
    monkeypatch.setattr("app.services.bounce_guard.check", lambda addr: (False, "suppressed"))
    out = email_sender.send_email(_email())
    assert out.status == "failed" and "bounce guard" in out.error


def test_auto_send_room_zero_when_switch_off(monkeypatch):
    from app.services import auto_send
    monkeypatch.setattr(auto_send, "outstanding", lambda db: 0)
    monkeypatch.setenv("EMAIL_DAILY_CAP", "15")
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", "1")
    assert auto_send.room(None) == 15
    monkeypatch.setenv("EMAIL_SENDING_ENABLED", "0")
    assert auto_send.room(None) == 0


def test_daily_cap_still_holds(monkeypatch):
    monkeypatch.setenv("EMAIL_DAILY_CAP", "2")
    hp.record_daily_sent(); hp.record_daily_sent()
    ok, why = hp.daily_cap_ok()
    assert ok is False and "cap 2" in why
