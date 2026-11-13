"""29 Oct 2026 (lead 1043): segment from the typed division, not the 'corporate'
default; only name/company travel to Klaviyo; the Klaviyo profile is subscribed
from the AI-call consent (time + call id) and the event waits until Klaviyo
reports it subscribed; an undelivered Klaviyo event can be marked so the 7-day
rule does not count it. No network."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import warm_followup as wf
from app.services import whatsapp_klaviyo as k


def L(**kw):
    base = dict(segment="corporate", division="", industry="", category="", company="Acme Pvt Ltd")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("kw,expected", [
    (dict(division="modern_trade", industry="Modern Trade", company="Frontier Stores"), "retail"),   # 1043
    (dict(division="supermarket"), "retail"),
    (dict(division="retail_kirana"), "retail"),
    (dict(division="restaurant", company="Jamna Grocery Store"), "other"),
    (dict(division="cafe", company="City Mart"), "other"),
    (dict(division="caterer"), "other"),
    (dict(division="corporate_office"), "corporate"),
    (dict(division="wholesaler"), "distributor"),
    (dict(segment="distributor"), "distributor"),
    (dict(segment="horeca"), "other"),
    (dict(), "other"),                                    # bare 'corporate' default -> no figure
    (dict(company="Jamna Grocery Store"), "retail"),
    (dict(company="New Bharat Mart"), "retail"),
])
def test_segment_uses_division_not_default(kw, expected):
    assert wf._segment(L(**kw)) == expected


def test_1043_margin_line_is_retail():
    lead = L(division="modern_trade", industry="Modern Trade", company="Frontier Stores")
    assert wf.margin_line(lead) == "For retail the margin is 30% (25% trade discount + 5% cash discount)."
    assert "Corporate" not in wf.margin_line(lead)
    assert "below retail" not in wf.margin_line(L())


# ------------------------------------------------------------ klaviyo
class Resp:
    def __init__(s, code, data=None): s.status_code = code; s._d = data or {}
    def json(s): return s._d


class FakeHttp:
    def __init__(s, subscribed=False, profile=True):
        s.posts = []; s.gets = 0; s.subscribed = subscribed; s.profile = profile
    def get(s, url, headers=None, params=None, timeout=None):
        s.gets += 1
        if not s.profile:
            return Resp(200, {"data": []})
        m = {"consent": "SUBSCRIBED", "can_receive": True} if s.subscribed else \
            {"consent": "NEVER_SUBSCRIBED", "can_receive": False}
        return Resp(200, {"data": [{"id": "P1", "attributes": {"subscriptions": {"whatsapp": {"marketing": m}}}}]})
    def post(s, url, json=None, headers=None, timeout=None):
        s.posts.append((url.split("/api")[1], json))
        if url.endswith("/profile-import"):
            return Resp(200, {"data": {"id": "P1"}})
        return Resp(202)


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("WHATSAPP_PROVIDER", "klaviyo")
    monkeypatch.setenv("KLAVIYO_WA_ENABLED", "1")
    monkeypatch.setenv("KLAVIYO_WA_FLOW_LIVE", "1")
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "pk_test")
    monkeypatch.delenv("KLAVIYO_WA_AUTO_SUBSCRIBE", raising=False)
    monkeypatch.setattr(k, "_ATTEMPTS", str(tmp_path / "att.json"))
    h = FakeHttp()
    monkeypatch.setattr(k, "httpx", h)
    return h


LEAD = SimpleNamespace(id=1043, consent_phone="9876500009", whatsapp_number="9876500009")
CONSENT = k.CallConsent(consented_at=datetime(2026, 10, 29, 11, 28, 37, tzinfo=timezone.utc),
                        call_id="ppp-1043-abc")
PARAMS = {"name": "", "company": "Frontier Stores", "review_text": "Corporate pricing is 20-30% below retail.",
          "class": "INTERESTED", "flags": ["x"], "followup_type": "warm_followup"}


def test_event_carries_only_name_and_company(live):
    live.subscribed = True
    r = k.emit(LEAD, "d1", "pb_b2b_fullrange_hinglish", PARAMS, consent=CONSENT)
    assert r.status == "queued_to_klaviyo"
    url, body = live.posts[-1]
    assert url == "/events"
    props = body["data"]["attributes"]["properties"]
    assert set(props) == {"template_key", "draft_id", "name", "company"}
    assert "Corporate" not in json.dumps(body)


def test_emit_refuses_without_call_consent(live):
    live.subscribed = True
    r = k.emit(LEAD, "d1", "t", PARAMS)
    assert r.status == "blocked" and not live.posts and live.gets == 0


def test_unsubscribed_profile_is_subscribed_then_held(live):
    r = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r.status == "blocked" and "submitted" in r.reason and not r.message_id
    urls = [u for u, _ in live.posts]
    assert urls == ["/profile-import", "/profile-subscription-bulk-create-jobs"]       # no /events
    props = live.posts[0][1]["data"]["attributes"]["properties"]
    assert props["wa_consent_call_id"] == "ppp-1043-abc" and props["wa_consent_source"] == "AI_CALL"
    assert props["wa_consented_at"].startswith("2026-10-29T11:28:37")
    job = live.posts[1][1]["data"]
    assert "relationships" not in job
    a = job["attributes"]
    assert a["historical_import"] is True and "ppp-1043-abc" in a["custom_source"]
    p = a["profiles"]["data"][0]
    assert p["id"] == "P1"
    assert p["attributes"]["subscriptions"]["whatsapp"]["marketing"] == {
        "consent": "SUBSCRIBED", "consented_at": CONSENT.consented_at.isoformat()}
    # next tick inside the retry window: no new job, still held
    r2 = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r2.status == "blocked" and "pending" in r2.reason and len(live.posts) == 2
    # Klaviyo applied it: the event goes
    live.subscribed = True
    r3 = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r3.status == "queued_to_klaviyo" and live.posts[-1][0] == "/events"


def test_gives_up_after_max_tries(live, monkeypatch):
    monkeypatch.setattr(k, "SUBSCRIBE_RETRY_MIN", 0)
    for _ in range(k.SUBSCRIBE_MAX_TRIES):
        k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    n = len(live.posts)
    r = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r.status == "blocked" and "attempts" in r.reason and len(live.posts) == n


def test_auto_subscribe_switch_off(live, monkeypatch):
    monkeypatch.setenv("KLAVIYO_WA_AUTO_SUBSCRIBE", "0")
    r = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r.status == "blocked" and "auto-subscribe is off" in r.reason and not live.posts


def test_lookup_failure_never_emits(live, monkeypatch):
    def boom(*a, **kw): raise RuntimeError("x")
    monkeypatch.setattr(live, "get", boom)
    r = k.emit(LEAD, "d1", "t", PARAMS, consent=CONSENT)
    assert r.status == "blocked" and not live.posts


class FakeDB:
    def __init__(s, call_id): s.call_id = call_id
    def execute(s, *a, **kw):
        return SimpleNamespace(first=lambda: (s.call_id,) if s.call_id else None)
    def query(s, *a):
        class Q:
            def filter(q, *a): return q
            def order_by(q, *a): return q
            def first(q): return None
        return Q()


def CL(**kw):
    base = dict(id=1043, consent_status="EXPLICIT", consent_source="AI_CALL_WHATSAPP_REQUEST",
                do_not_call=False, whatsapp_verified=None,
                consent_timestamp=datetime.utcnow() - timedelta(hours=1))
    base.update(kw)
    return SimpleNamespace(**base)


def test_call_consent_clear_record():
    c, why = k.call_consent(FakeDB("ppp-1043-abc"), CL())
    assert c and c.call_id == "ppp-1043-abc" and c.consented_at.tzinfo is not None


@pytest.mark.parametrize("kw,why", [
    (dict(consent_status="IMPLIED"), "EXPLICIT"),
    (dict(consent_source="MANUAL"), "AI call"),
    (dict(do_not_call=True), "do_not_call"),
    (dict(whatsapp_verified=False), "not on WhatsApp"),
    (dict(consent_timestamp=None), "timestamp"),
    (dict(consent_timestamp=datetime.utcnow() + timedelta(hours=1)), "future"),
])
def test_call_consent_never_inferred(kw, why):
    c, reason = k.call_consent(FakeDB("ppp-1"), CL(**kw))
    assert c is None and why in reason


def test_call_consent_needs_call_id():
    c, reason = k.call_consent(FakeDB(None), CL())
    assert c is None and "call id" in reason


# ------------------------------------------------------------ pacer 7-day
def test_not_delivered_frees_only_that_event(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from conftest import memory_engine
    from app.models.models import Base, B2BLead, WorkflowEvent
    from app.services import whatsapp_pacer as p
    eng = memory_engine(); Base.metadata.create_all(eng); db = sessionmaker(bind=eng)()
    monkeypatch.setattr(p, "BLOCK_FILES", []); monkeypatch.setattr(p, "STRIKE_FILES", [])
    lead = B2BLead(id=550, company="F", phone="9876500009", consent_phone="9876500009", whatsapp_number="9876500009",
                   consent_status="EXPLICIT", consent_source="AI_CALL_WHATSAPP_REQUEST",
                   consent_timestamp=datetime.utcnow() - timedelta(hours=2))
    db.add(lead); db.commit()
    monkeypatch.setattr("app.services.whatsapp_sender.consent_check", lambda l: (True, "ok"))
    now = datetime.utcnow()
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_QUEUED_TO_KLAVIYO", actor="SYSTEM",
                         channel="whatsapp", payload={"unique_id": "pb-wa-1-a"}, occurred_at=now)); db.commit()
    assert p.eligible(db, lead) == (False, "messaged in the last 7 days")
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_NOT_DELIVERED", actor="FOUNDER",
                         channel="whatsapp", payload={"unique_id": "pb-wa-1-a"}, occurred_at=now)); db.commit()
    assert p.eligible(db, lead) == (True, "eligible")
    db.add(WorkflowEvent(lead_id=lead.id, event_type="WHATSAPP_QUEUED_TO_KLAVIYO", actor="SYSTEM",
                         channel="whatsapp", payload={"unique_id": "pb-wa-1-b"}, occurred_at=now)); db.commit()
    assert p.eligible(db, lead)[0] is False                 # a different event still counts
    db.close(); eng.dispose()
