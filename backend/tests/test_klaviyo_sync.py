import json, pytest
from app.services import klaviyo_sync as k

class R:
    def __init__(s, code, data=None): s.status_code=code; s._d=data or {}; s.headers={}; s.text=json.dumps(s._d)
    def json(s): return s._d

class Sess:
    def __init__(s, script): s.script=list(script); s.calls=[]
    def request(s, method, url, headers=None, data=None, timeout=None):
        s.calls.append((method, url.split("/api")[1], json.loads(data) if data else None))
        return s.script.pop(0) if s.script else R(202)

LEAD = {"id": 9001, "company": "Test Agencies", "contact_name": "Ram Kumar", "city": "Agra",
        "segment": "distributor", "phone": "9876543210", "whatsapp_number": "+919876543210"}

@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KLAVIYO_PRIVATE_KEY", "pk_test")
    monkeypatch.setattr(k, "MARKER", tmp_path / "m.json")

def test_e164():
    assert k.to_e164("098765 43210") == "+919876543210"
    assert k.to_e164("919876543210") == "+919876543210"
    assert k.to_e164("12345") is None

def test_full_sync_and_idempotent():
    s = Sess([R(200, {"data": {"id": "P1"}})])
    r = k.sync_lead(LEAD, session=s, sleep=lambda x: None)
    assert r["ok"] and r["steps"] == ["profile", "wa_consent", "list:Vvfcpj", "list:R3aC2j", "event"]
    sub = s.calls[1][2]["data"]
    assert "relationships" not in sub                       # 29 Oct: no list on the consent job
    assert s.calls[2][1] == "/lists/Vvfcpj/relationships/profiles"
    assert sub["attributes"]["profiles"]["data"][0]["attributes"]["subscriptions"]["whatsapp"]["marketing"]["consent"] == "SUBSCRIBED"
    ev = s.calls[4][2]["data"]["attributes"]
    assert ev["unique_id"] == "wa-optin-9001" and ev["metric"]["data"]["attributes"]["name"] == k.METRIC
    s2 = Sess([])
    assert k.sync_lead(LEAD, session=s2)["skipped"] == "already synced" and s2.calls == []

def test_retries_429_and_5xx():
    s = Sess([R(429), R(503), R(200, {"data": {"id": "P1"}})])
    slept = []
    r = k.sync_lead(LEAD, session=s, sleep=slept.append)
    assert r["ok"] and len(slept) == 2

def test_failure_never_raises_and_masks():
    s = Sess([R(400, {"errors": ["bad"]})])
    r = k.sync_lead(LEAD, session=s, sleep=lambda x: None)
    assert not r["ok"] and "9876543210" not in json.dumps(r) and r["phone"] == "…3210"

def test_no_key(monkeypatch):
    monkeypatch.delenv("KLAVIYO_PRIVATE_KEY"); monkeypatch.setattr(k, "_key", lambda: None)
    assert k.sync_lead(LEAD, session=Sess([]))["error"] == "no KLAVIYO_PRIVATE_KEY"

def test_schedule_never_raises():
    k.schedule(object())  # garbage lead: must not raise


def test_delivery_sync_matches_failure_to_one_exact_lead_send(monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Exact Match Cafe", phone="+918406346161",
                       whatsapp_number="+918406346161", consent_phone="+918406346161")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX6161",
            status="QUEUED_TO_KLAVIYO", whatsapp_message_id="pb-wa-test-draft",
            created_at=datetime(2026, 10, 31, 4, 3, 3))
        db.add(row); db.commit()
        class Response:
            def json(self):
                return {"data": [{
                    "id": "provider-failure-1",
                    "attributes": {"datetime": "2026-10-31T04:03:16+00:00",
                        "event_properties": {"$flow": "TUrAGc", "$message": "VGRucF",
                            "To Number": "+918406346161", "Failure Source": "undelivered",
                            "Vendor Error Code": "131026", "Failure Type": "Message Undeliverable."}},
                    "relationships": {"profile": {"data": {"type": "profile", "id": "profile-1"}}}
                }], "included": [{"type": "profile", "id": "profile-1",
                    "attributes": {"external_id": f"lead-{lead.id}"}}]}
        class Empty:
            def json(self): return {"data": [], "included": []}
        monkeypatch.setattr(k, "_request", lambda method, path, **kw: Response() if "UtYn7N" in path else Empty())
        result = k.sync_klaviyo_delivery_events(db)
        db.refresh(row)
        assert result["failed_updated"] == 1
        assert row.status == "FAILED"
        assert row.failed_at == datetime(2026, 10, 31, 4, 3, 16)
        assert "provider-failure-1" in row.whatsapp_response
    finally:
        db.close()
        engine.dispose()


def test_delivery_sync_does_not_match_wrong_number_or_ambiguous_sends(monkeypatch):
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="No Guess Cafe", phone="+919999999999",
                       whatsapp_number="+919999999999")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX9999",
            status="QUEUED_TO_KLAVIYO", created_at=datetime(2026, 10, 31, 4, 3, 3))
        db.add(row); db.commit()
        class Response:
            def json(self):
                return {"data": [{
                    "id": "wrong-number-event",
                    "attributes": {"datetime": "2026-10-31T04:03:16+00:00",
                        "event_properties": {"$flow": "TUrAGc", "To Number": "+918406346161",
                            "Failure Source": "undelivered"}},
                    "relationships": {"profile": {"data": {"type": "profile", "id": "profile-1"}}}
                }], "included": [{"type": "profile", "id": "profile-1",
                    "attributes": {"external_id": f"lead-{lead.id}"}}]}
        class Empty:
            def json(self): return {"data": [], "included": []}
        monkeypatch.setattr(k, "_request", lambda method, path, **kw: Response() if "UtYn7N" in path else Empty())
        result = k.sync_klaviyo_delivery_events(db)
        db.refresh(row)
        assert result["failed_updated"] == 0
        assert row.status == "QUEUED_TO_KLAVIYO"
    finally:
        db.close()
        engine.dispose()


def test_relayed_event_marks_sent_but_not_delivered(monkeypatch):
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine
    engine = memory_engine(); Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Relay Cafe", phone="+919111122233", whatsapp_number="+919111122233")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX2233", status="QUEUED_TO_KLAVIYO",
            created_at=datetime(2026,11,2,11,27,3))
        db.add(row); db.commit()
        class Empty:
            def json(self): return {"data": [], "included": []}
        class Relay:
            def json(self):
                return {"data":[{"id":"relay-1","attributes":{"datetime":"2026-11-02T11:27:27+00:00",
                    "event_properties":{"$flow":"TUrAGc","$message":"VGRucF","To Number":"+919111122233"}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p1"}}}}],
                    "included":[{"type":"profile","id":"p1","attributes":{"external_id":f"lead-{lead.id}"}}]}
        monkeypatch.setattr(k,"_request",lambda method,path,**kw: Relay() if "VJMsMV" in path else Empty())
        result=k.sync_klaviyo_delivery_events(db); db.refresh(row)
        assert result["relayed_updated"] == 1
        assert row.status == "SENT" and row.sent_at == datetime(2026,11,2,11,27,27)
        assert row.delivered_at is None
        assert '"delivery_confirmed": false' in row.whatsapp_response
    finally:
        db.close(); engine.dispose()


def test_failure_event_wins_over_relay_event_for_same_send(monkeypatch):
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine
    engine = memory_engine(); Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Failure Wins Cafe", phone="+919222233344", whatsapp_number="+919222233344")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX3344", status="QUEUED_TO_KLAVIYO",
            created_at=datetime(2026,11,2,11,27,3))
        db.add(row); db.commit()
        class Failure:
            def json(self):
                return {"data":[{"id":"fail-1","attributes":{"datetime":"2026-11-02T11:27:16+00:00",
                    "event_properties":{"$flow":"TUrAGc","$message":"VGRucF","To Number":"+919222233344",
                    "Failure Source":"undelivered","Vendor Error Code":"131026"}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p2"}}}}],
                    "included":[{"type":"profile","id":"p2","attributes":{"external_id":f"lead-{lead.id}"}}]}
        class Relay:
            def json(self):
                return {"data":[{"id":"relay-1","attributes":{"datetime":"2026-11-02T11:27:27+00:00",
                    "event_properties":{"$flow":"TUrAGc","$message":"VGRucF","To Number":"+919222233344"}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p2"}}}}],
                    "included":[{"type":"profile","id":"p2","attributes":{"external_id":f"lead-{lead.id}"}}]}
        monkeypatch.setattr(k,"_request",lambda method,path,**kw: Failure() if "UtYn7N" in path else Relay())
        result=k.sync_klaviyo_delivery_events(db); db.refresh(row)
        assert result["failed_updated"] == 1
        assert result["failure_precedence"] == 1
        assert row.status == "FAILED"
        assert row.failed_at == datetime(2026,11,2,11,27,16)
    finally:
        db.close(); engine.dispose()


def test_bsuid_uses_exact_profile_phone_and_does_not_mark_delivered(monkeypatch):
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine
    engine = memory_engine(); Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="BSUID Cafe", phone="+919111122233", whatsapp_number="+919111122233")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX2233", status="QUEUED_TO_KLAVIYO",
            created_at=datetime(2026,11,2,11,27,3))
        db.add(row); db.commit()
        bsuid = "1599990000111222.IN"
        class Empty:
            def json(self): return {"data": [], "included": []}
        class Relay:
            def json(self):
                return {"data":[{"id":"bsuid-relay-1","attributes":{"datetime":"2026-11-02T11:27:27+00:00",
                    "event_properties":{"$flow":"TUrAGc","$message":"VGRucF","To Number":bsuid,
                        "WhatsApp BSUID":bsuid}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p1"}}}}],
                    "included":[{"type":"profile","id":"p1","attributes":{
                        "external_id":f"lead-{lead.id}","phone_number":"+919111122233"}}]}
        monkeypatch.setattr(k,"_request",lambda method,path,**kw: Relay() if "VJMsMV" in path else Empty())
        result=k.sync_klaviyo_delivery_events(db); db.refresh(row)
        assert result["relayed_updated"] == 1
        assert row.status == "SENT"
        assert row.sent_at == datetime(2026,11,2,11,27,27)
        assert row.delivered_at is None
        body=json.loads(row.whatsapp_response)
        assert body["destination_evidence"] == "profile_phone_for_bsuid"
        assert body["delivery_confirmed"] is False
        assert bsuid not in row.whatsapp_response
    finally:
        db.close(); engine.dispose()


def test_bsuid_profile_phone_mismatch_stays_queued(monkeypatch):
    from datetime import datetime
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base, B2BLead, OutboundWhatsApp
    from conftest import memory_engine
    engine = memory_engine(); Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    try:
        lead = B2BLead(company="Mismatch Cafe", phone="+919111122233", whatsapp_number="+919111122233")
        db.add(lead); db.commit()
        row = OutboundWhatsApp(lead_id=lead.id, recipient="XXXXXX2233", status="QUEUED_TO_KLAVIYO",
            created_at=datetime(2026,11,2,11,27,3))
        db.add(row); db.commit()
        bsuid = "1599990000111999.IN"
        class Empty:
            def json(self): return {"data": [], "included": []}
        class Relay:
            def json(self):
                return {"data":[{"id":"bsuid-mismatch-1","attributes":{"datetime":"2026-11-02T11:27:27+00:00",
                    "event_properties":{"$flow":"TUrAGc","To Number":bsuid,"WhatsApp BSUID":bsuid}},
                    "relationships":{"profile":{"data":{"type":"profile","id":"p1"}}}}],
                    "included":[{"type":"profile","id":"p1","attributes":{
                        "external_id":f"lead-{lead.id}","phone_number":"+918888888888"}}]}
        monkeypatch.setattr(k,"_request",lambda method,path,**kw: Relay() if "VJMsMV" in path else Empty())
        result=k.sync_klaviyo_delivery_events(db); db.refresh(row)
        assert result["relayed_updated"] == 0
        assert result["unmatched"] == 1
        assert row.status == "QUEUED_TO_KLAVIYO"
        assert row.sent_at is None
    finally:
        db.close(); engine.dispose()
