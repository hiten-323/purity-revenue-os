"""Gate 1 closure: unrecoverable identity, blocked queue, permanent unmatched events."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import gate1_holds as g


def _lead(**kw):
    base = dict(id=1, company="Cafe", phone="+919811122233", email="a@b.in",
                whatsapp_number=None, consent_phone=None, do_not_call=0,
                consent_status="EXPLICIT", status="DISCOVERED", contact_status="CONTACTABLE")
    base.update(kw)
    return SimpleNamespace(**base)


def test_close_unrecoverable_keeps_dnc_unknown_consent_and_adds_no_contact():
    now = "2026-11-06 07:40:00"
    current = dict(id=2305, company="[RECOVERY HOLD] Unknown business — orphan lead #2305",
                   phone=None, email=None, whatsapp_number=None, consent_phone=None,
                   do_not_call=1, dnc_reason="ORPHAN_IDENTITY_MISSING",
                   consent_status="UNKNOWN", contact_status="QUARANTINED", status="QUARANTINED")
    closed = g.close_unrecoverable_fields(current, now)
    assert closed["status"] == "CLOSED_UNRECOVERABLE"
    assert closed["do_not_call"] == 1
    assert closed["consent_status"] == "UNKNOWN"
    assert closed["contact_status"] == "QUARANTINED"
    assert closed["phone"] is None and closed["email"] is None
    assert closed["company"] == current["company"]
    event = closed["event"]
    assert event["actor"] == "GATE1_REMEDIATION"
    assert event["occurred_at"] == now
    assert event["payload"]["reason"] == "identity not recovered; masked suffix is not identity."
    assert event["payload"]["identity_recovered"] is False


def test_close_refuses_when_a_phone_or_email_exists():
    current = dict(do_not_call=1, consent_status="UNKNOWN", phone="999", email=None,
                   whatsapp_number=None, consent_phone=None, contact_status="QUARANTINED",
                   status="QUARANTINED", company="x", dnc_reason="x")
    with pytest.raises(RuntimeError):
        g.close_unrecoverable_fields(current, "2026-11-06 07:40:00")


def test_queued_row_blocks_without_becoming_sent_or_delivered():
    blocked = g.block_queue_without_provider_event(
        "QUEUED_TO_KLAVIYO", '{"status":"queued_to_klaviyo","draft_id":"53fac4cbd5"}')
    assert blocked["status"] == "BLOCKED_NO_PROVIDER_EVENT"
    body = json.loads(blocked["whatsapp_response"])
    assert body["reason"] == "no provider event in the 24-hour window."
    assert body["terminal"] is True and body["requeue"] is False
    assert body["prior_response"].startswith('{"status":"queued_to_klaviyo"')
    assert "SENT" not in blocked["status"]
    assert "DELIVERED" not in blocked["status"]
    assert "RELAYED" not in blocked["status"]


def test_unmatched_reasons_and_permanent_status_are_not_a_join():
    assert g.unmatched_reason_allowed("no_local_send_in_24h_window")
    assert g.unmatched_reason_allowed("no_unique_local_lead_for_destination")
    assert g.unmatched_reason_allowed("no_unique_local_lead")
    assert not g.unmatched_reason_allowed("missing_destination_number")
    assert g.PERMANENTLY_UNMATCHED == "PERMANENTLY_UNMATCHED"


def test_dnc_revoked_wrong_number_and_unknown_consent_are_not_send_ready():
    assert g.is_send_ready(_lead(do_not_call=1)) == (False, "do_not_call")
    revoked = _lead(consent_status="REVOKED", status="WRONG_NUMBER", do_not_call=1)
    assert g.is_send_ready(revoked)[0] is False
    correction = g.contact_status_correction(_lead(do_not_call=1, consent_status="REVOKED", status="WRONG_NUMBER"))
    assert correction[0] == "DO_NOT_CONTACT"
    assert "do_not_call" in correction[1] and "REVOKED" in correction[1] and "WRONG_NUMBER" in correction[1]
    unknown = _lead(consent_status="UNKNOWN", do_not_call=0, status="DISCOVERED")
    assert g.is_send_ready(unknown)[0] is False
    assert g.contact_status_correction(unknown)[0] == "DO_NOT_CONTACT"
    explicit = _lead()
    assert g.is_send_ready(explicit)[0] is True
    assert g.contact_status_correction(explicit) is None
    # A non-CONTACTABLE row is not relabelled.
    assert g.contact_status_correction(_lead(contact_status="SUPPRESSED", do_not_call=1)) is None


def test_unknown_consent_reminder_is_rejected_even_if_it_is_scheduled():
    lead = _lead(id=201, consent_status="UNKNOWN", do_not_call=0)
    ok, why = g.reminder_may_dispatch(lead, "whatsapp", "SCHEDULED")
    assert ok is False and why == "consent_status=UNKNOWN"
    for lead_id in (171, 93):
        ok, why = g.reminder_may_dispatch(_lead(id=lead_id, consent_status="UNKNOWN"), "whatsapp", "SCHEDULED")
        assert ok is False
    # A rejected row stays rejected if a later caller asks again.
    ok, why = g.reminder_may_dispatch(lead, "whatsapp", "REJECTED_NO_CONSENT")
    assert ok is False and why == "reminder is not scheduled"
    ready = _lead(consent_status="EXPLICIT")
    assert g.reminder_may_dispatch(ready, "whatsapp", "SCHEDULED")[0] is True


def test_permanent_unmatched_event_cannot_attach_to_the_queued_send(monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from app.services import klaviyo_sync as k
    from conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Held Cafe", phone="+919111122233", whatsapp_number="+919111122233",
                       consent_status="EXPLICIT")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX2233", status="QUEUED_TO_KLAVIYO",
            created_at=datetime(2026, 11, 2, 11, 27, 3))
        db.add(row); db.commit()
        db.execute(text("""CREATE TABLE IF NOT EXISTS klaviyo_delivery_quarantine (
            provider_event_id TEXT PRIMARY KEY, flow_id TEXT, event_type TEXT,
            event_at TEXT, profile_external_id TEXT, destination_last4 TEXT,
            vendor_error_code TEXT, failure_type TEXT, reason TEXT,
            recorded_at TEXT NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'OPEN'
        )"""))
        db.execute(text("""INSERT INTO klaviyo_delivery_quarantine
            (provider_event_id,flow_id,event_type,event_at,reason,recorded_at,resolution_status)
            VALUES('locked-event','TUrAGc','RELAYED','2026-11-02T11:27:27+00:00',
                   'no_local_send_in_24h_window','2026-11-06T07:00:00','PERMANENTLY_UNMATCHED')"""))
        db.commit()

        class Empty:
            def json(self):
                return {"data": [], "included": []}
        class Relay:
            def json(self):
                return {"data":[{"id":"locked-event","attributes":{"datetime":"2026-11-02T11:27:27+00:00",
                    "event_properties":{"$flow":"TUrAGc","$message":"VGRucF","To Number":"+919111122233"}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p1"}}}}],
                    "included":[{"type":"profile","id":"p1","attributes":{"external_id":f"lead-{lead.id}","phone_number":"+919111122233"}}]}
        monkeypatch.setattr(k, "_request", lambda method, path, **kw: Relay() if "VJMsMV" in path else Empty())
        result = k.sync_klaviyo_delivery_events(db)
        db.refresh(row)
        assert result["permanently_unmatched"] == 1
        assert result["relayed_updated"] == 0
        assert row.status == "QUEUED_TO_KLAVIYO"
        still = db.execute(text("SELECT resolution_status FROM klaviyo_delivery_quarantine WHERE provider_event_id='locked-event'")).scalar()
        assert still == "PERMANENTLY_UNMATCHED"
    finally:
        db.close(); engine.dispose()


def test_blocked_queue_row_is_not_moved_to_sent(monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from app.services import klaviyo_sync as k
    from conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Blocked Cafe", phone="+919111122233", whatsapp_number="+919111122233",
                       consent_status="EXPLICIT")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX2233", status="BLOCKED_NO_PROVIDER_EVENT",
            whatsapp_response='{"reason":"no provider event in the 24-hour window.","prior_response":"{}"}',
            created_at=datetime(2026, 11, 3, 7, 50, 38), sent_at=datetime(2026, 11, 3, 7, 50, 38))
        db.add(row); db.commit()
        class Empty:
            def json(self):
                return {"data": [], "included": []}
        class Relay:
            def json(self):
                return {"data":[{"id":"late-event","attributes":{"datetime":"2026-11-03T08:00:00+00:00",
                    "event_properties":{"$flow":"TUrAGc","To Number":"+919111122233"}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p1"}}}}],
                    "included":[{"type":"profile","id":"p1","attributes":{"external_id":f"lead-{lead.id}"}}]}
        monkeypatch.setattr(k, "_request", lambda method, path, **kw: Relay() if "VJMsMV" in path else Empty())
        result = k.sync_klaviyo_delivery_events(db)
        db.refresh(row)
        assert row.status == "BLOCKED_NO_PROVIDER_EVENT"
        assert row.delivered_at is None
        assert result["relayed_updated"] == 0
    finally:
        db.close(); engine.dispose()
