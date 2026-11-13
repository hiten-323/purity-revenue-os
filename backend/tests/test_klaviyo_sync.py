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
