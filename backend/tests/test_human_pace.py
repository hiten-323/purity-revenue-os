from app.services import human_pace as hp


def test_email_gap_and_hourly_cap(monkeypatch):
    monkeypatch.setenv("EMAIL_HOURLY_CAP_MIN", "4"); monkeypatch.setenv("EMAIL_HOURLY_CAP_MAX", "4")
    t = 1_800_000_000 - (1_800_000_000 % 3600) + 1800 - 19800 % 3600  # some instant
    assert hp.may_send_email(t)[0]
    hp.record_email_sent(t)
    ok, why = hp.may_send_email(t + 60)
    assert not ok and "gap" in why
    assert hp.may_send_email(t + 721)[0] or "cap" in hp.may_send_email(t + 721)[1]


def test_cap_blocks_within_hour(monkeypatch):
    monkeypatch.setenv("EMAIL_HOURLY_CAP_MIN", "2"); monkeypatch.setenv("EMAIL_HOURLY_CAP_MAX", "2")
    monkeypatch.setenv("EMAIL_GAP_MIN_SECONDS", "0"); monkeypatch.setenv("EMAIL_GAP_MAX_SECONDS", "0")
    t = 1_800_000_000 - (1_800_000_000 % 3600)
    for i in range(2):
        assert hp.may_send_email(t + i)[0]; hp.record_email_sent(t + i)
    ok, why = hp.may_send_email(t + 5)
    assert not ok and "cap" in why


def test_call_gap_random_3_to_8_min():
    for _ in range(50):
        assert 180 <= hp.call_tick_seconds(120) <= 480
    hp.record_dial_attempt(True, now=0.0)
    assert not hp.may_dial(179.0) and hp.may_dial(481.0)
    hp.record_dial_attempt(False, now=1000.0)
    assert hp.may_dial(1000.0)


def test_disabled_restores_old(monkeypatch):
    monkeypatch.setenv("HUMAN_PACE_ENABLED", "0")
    hp.record_email_sent(0)
    assert hp.may_send_email(1)[0] and hp.call_tick_seconds(120) == 120
