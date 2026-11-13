"""28 Oct 2026: Klaviyo-as-BSP WhatsApp path — gates, masking, consent push. No network."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import whatsapp_pacer as p, whatsapp_klaviyo as k, whatsapp_drafts as wd
from app.services import klaviyo_sync as ks
from app.services import founder_call_pipeline as fcp
from app.services import whatsapp_sender as ws

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for v in ("WHATSAPP_PROVIDER", "KLAVIYO_WA_ENABLED", "KLAVIYO_WA_FLOW_LIVE", "WHATSAPP_PACER_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(wd, "STORE", tmp_path / "drafts.json")
    bl = tmp_path / "block.txt"; bl.write_text("# hdr\n9876500000\n+91 98765 00002\n")
    st = tmp_path / "strike.json"; st.write_text(json.dumps({"919876500001": ["x"]}))
    monkeypatch.setattr(p, "BLOCK_FILES", [str(bl)])
    monkeypatch.setattr(p, "STRIKE_FILES", [str(st)])


def test_hours_mon_sat_0930_1900():
    assert p.in_hours(datetime(2026, 10, 29, 9, 30, tzinfo=IST))
    assert p.in_hours(datetime(2026, 10, 31, 20, 0, tzinfo=IST))      # Saturday
    assert not p.in_hours(datetime(2026, 10, 29, 20, 1, tzinfo=IST))
    assert not p.in_hours(datetime(2026, 10, 29, 9, 29, tzinfo=IST))
    assert not p.in_hours(datetime(2026, 11, 1, 12, 0, tzinfo=IST))   # Sunday


def test_gap_3_to_12_min_and_cap(monkeypatch):
    for m in range(50):
        g = p._gap_for(datetime(2026, 10, 29, 10, m))
        assert 300 <= g <= 900
    monkeypatch.setenv("WA_MAX_PER_HOUR", "50"); assert p.max_per_hour() == 6
    monkeypatch.delenv("WA_MAX_PER_HOUR"); assert p.max_per_hour() == 6


def test_number_suppression():
    assert p.number_suppressed("+91 85919 77190") == "hard-blocked"
    assert p.number_suppressed("9876500000") == "on the block list / DND"
    assert p.number_suppressed("09876500002") == "on the block list / DND"
    assert p.number_suppressed("9876500001") == "on the SIP strike / invalid list"
    assert p.number_suppressed("9876500009") == ""


def test_klaviyo_off_by_default_and_provider_default(monkeypatch):
    """Klaviyo is the default (and only) provider since AiSensy was retired,
    but it stays OFF until every switch is set; no .env is consulted here."""
    import dotenv
    monkeypatch.setattr(dotenv, "dotenv_values", lambda *a, **kw: {})
    monkeypatch.delenv("KLAVIYO_PRIVATE_KEY", raising=False)
    assert ws.provider() == "klaviyo"
    ok, why = k.config_status()
    assert ok is False and "KLAVIYO_WA_ENABLED" in why
    assert ws.is_configured() is False
    # Each switch alone is not enough.
    monkeypatch.setenv("KLAVIYO_WA_ENABLED", "1")
    assert k.config_status() == (False, "KLAVIYO_PRIVATE_KEY missing")
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "pk_test")
    assert k.config_status()[0] is False and "FLOW_LIVE" in k.config_status()[1]
    monkeypatch.setenv("KLAVIYO_WA_FLOW_LIVE", "1")
    assert k.config_status()[0] is True and ws.is_configured() is True
    # The retired AiSensy switch cannot arm WhatsApp under another provider name.
    monkeypatch.setenv("WHATSAPP_PROVIDER", "aisensy")
    monkeypatch.setenv("AISENSY_ENABLED", "0")
    assert k.config_status()[0] is False


def test_drafts_need_founder_approval():
    did = wd.create(7, "pb_catalogue", {"name": "Ram"})
    assert wd.approved_for(did, 7) is None
    with pytest.raises(ValueError):
        wd.approve(did, "")
    wd.approve(did, "Hiten")
    assert wd.approved_for(did, 7) and wd.approved_for(did, 8) is None
    wd.mark_sent(did, "u1")
    assert wd.approved_for(did, 7) is None


def test_klaviyo_path_blocks_without_draft(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PROVIDER", "klaviyo")
    monkeypatch.setenv("KLAVIYO_WA_ENABLED", "1")
    monkeypatch.setenv("KLAVIYO_WA_FLOW_LIVE", "1")
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "pk_test")
    monkeypatch.setattr(ws, "consent_check", lambda lead: (True, "ok"))
    called = []
    monkeypatch.setattr(k, "emit", lambda *a, **kw: called.append(a))
    lead = SimpleNamespace(id=1, consent_phone="9876500009")
    r = ws.send_whatsapp(lead, "", db=object())
    assert r.status == "blocked" and not called


def test_klaviyo_path_not_configured_when_flags_off(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PROVIDER", "klaviyo")
    monkeypatch.setattr(ws, "consent_check", lambda lead: (True, "ok"))
    r = ws.send_whatsapp(SimpleNamespace(id=1), "", db=object(), approved_draft_id="x")
    assert r.status == "not_configured"


def test_destination_masks_and_blocks():
    lead = SimpleNamespace(phone="9876500000")
    d, how, refusal = fcp._call_whatsapp_destination(lead, "")
    assert d == "" and "block list" in refusal and "9876500000" not in refusal and "XXXXXX0000" in refusal
    d, how, refusal = fcp._call_whatsapp_destination(SimpleNamespace(phone="9876500009"), "98765 00001")
    assert d == "" and "strike" in refusal and "9876500001" not in refusal
    d, how, refusal = fcp._call_whatsapp_destination(SimpleNamespace(phone="9876500000"), "9876500009")
    assert d == "9876500009" and refusal == ""          # separate number allowed; phone untouched
    d, how, refusal = fcp._call_whatsapp_destination(SimpleNamespace(phone="9876500009"), "12345")
    assert d == "" and "12345" not in refusal


def test_wa_consent_detail_is_reporting_only():
    assert fcp.clean_call_details({"wa_consent": "no"}) == {"wa_consent": "NO"}
    assert fcp.clean_call_details({"wa_consent": "maybe"}) == {}


class R:
    def __init__(s, code, data=None): s.status_code = code; s._d = data or {}; s.headers = {}; s.text = json.dumps(s._d)
    def json(s): return s._d


class Sess:
    def __init__(s, script): s.script = list(script); s.calls = []
    def request(s, method, url, headers=None, data=None, timeout=None):
        s.calls.append((method, url.split("/api")[1], json.loads(data) if data else None))
        return s.script.pop(0) if s.script else R(202)


def test_klaviyo_sync_sends_consent_time_and_call_id(tmp_path, monkeypatch):
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "pk_test")
    monkeypatch.setattr(ks, "MARKER", tmp_path / "m.json")
    lead = SimpleNamespace(id=42, company="X", contact_name="Ram K", city="Agra", segment="cafe",
                           email=None, phone="9876500009", whatsapp_number="9876500009",
                           consent_phone="9876500009", consent_timestamp=None)
    ts = datetime.utcnow() - timedelta(minutes=5)
    snap = ks.lead_snapshot(lead, call_ref="call-abc", consented_at=ts)
    s = Sess([R(200, {"data": {"id": "P1"}})])
    r = ks.sync_lead(snap, session=s, sleep=lambda x: None)
    assert r["ok"]
    prof = s.calls[0][2]["data"]["attributes"]["properties"]
    assert prof["wa_consent_call_id"] == "call-abc" and prof["wa_consent_source"] == "AI_CALL"
    job = s.calls[1][2]["data"]["attributes"]
    assert job["historical_import"] is True
    assert job["profiles"]["data"][0]["attributes"]["subscriptions"]["whatsapp"]["marketing"]["consented_at"].startswith(ts.strftime("%Y-%m-%dT%H:%M"))
    ev = s.calls[-1][2]["data"]["attributes"]["properties"]
    assert ev["call_id"] == "call-abc"


def test_future_consent_time_is_dropped():
    assert ks._iso_past(datetime.utcnow() + timedelta(hours=1)) is None
    assert ks._iso_past(None) is None


def test_email_reply_consent_is_eligible_for_whatsapp_pacer(monkeypatch):
    from app.services import whatsapp_pacer as pacer
    from app.services import outbound_cohort as cohort
    from tests.test_outbound_cohort_20261106 import _db, _lead
    db, engine = _db()
    try:
        lead = _lead(db, email="distributor@coffee.in", company="Coffee Dist")
        lead.phone = "+919876543210"
        lead.whatsapp_number = "+919876543210"
        lead.consent_phone = "+919876543210"
        lead.consent_status = "EXPLICIT"
        lead.consent_source = "EMAIL_REPLY_WHATSAPP_REQUEST"
        db.commit()

        monkeypatch.setattr(cohort, "on_allow_list", lambda l: True)
        monkeypatch.setattr(pacer, "number_suppressed", lambda n: "")

        ok, why = pacer.eligible(db, lead)
        assert ok is True
        assert why == "eligible"
    finally:
        db.close(); engine.dispose()
