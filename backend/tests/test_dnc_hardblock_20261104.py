"""4 Nov 2026: final dial chokepoint refuses never-dial numbers, DNC, SUPPRESSED
and duplicate-phone rows. Every check is a dry evaluation; nothing is dialled."""
import os
os.environ["AUTO_AI_CALLING_ENABLED"] = "0"
from types import SimpleNamespace
from app.services import voice_router, founder_call_pipeline as fcp


def _lead(**kw):
    base = dict(id=1, phone="+919811122233", do_not_call=False, contact_status="CONTACTABLE",
                contact_status_reason="", company="X Traders", city="Mohali", phone_trust="",
                whatsapp_number=None, consent_phone=None, dnc_reason=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _arm(monkeypatch):
    monkeypatch.setenv("AI_CALLING_ENABLED", "1")
    monkeypatch.delenv("AI_CALLING_KILL_SWITCH", raising=False)
    monkeypatch.setattr(fcp, "call_hours_open", lambda lead=None: (True, "test"))
    monkeypatch.setattr(voice_router, "config_status", lambda: (True, "ok"))


def test_never_dial_number_refused(monkeypatch):
    _arm(monkeypatch)
    r = voice_router.place_call(_lead(phone="+91 85919 77190"), dry_run=True)
    assert not r.placed and "DNC_BLOCKED" in r.error and "never dialled" in r.error


def test_dnc_refused(monkeypatch):
    _arm(monkeypatch)
    r = voice_router.place_call(_lead(do_not_call=True), dry_run=True)
    assert not r.placed and "DNC_BLOCKED" in r.error


def test_suppressed_duplicate_row_refused(monkeypatch):
    _arm(monkeypatch)
    r = voice_router.place_call(_lead(contact_status="SUPPRESSED",
                                      contact_status_reason="DUPLICATE_PHONE_ROW of lead 9"), dry_run=True)
    assert not r.placed and "DUPLICATE_PHONE_ROW" in r.error


def test_eligibility_refuses_never_dial():
    ok, why = fcp.may_place_ai_call(_lead(phone="8591977190"))
    assert not ok


def test_duplicate_reason_marker():
    assert fcp.duplicate_phone_reason(_lead(contact_status="SUPPRESSED",
                                            contact_status_reason="DUPLICATE_PHONE_ROW of lead 3"))
    assert fcp.duplicate_phone_reason(_lead()) is None  # detached object: no session, no block
