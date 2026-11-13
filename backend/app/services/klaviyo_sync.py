"""Klaviyo sync bridge for AI-call WhatsApp opt-ins.

When an AI call records WHATSAPP_OPT_IN (consent bound to a number), push the
lead to Klaviyo: profile (name/company/city/E.164 phone/segment), WhatsApp
marketing consent, lists Stockist Leads + WhatsApp Subscribers, and one
"Qualified Lead WhatsApp Opt-In" event.

Contract:
  * non-blocking: schedule() runs in a daemon thread; every failure is logged
    and swallowed, the call pipeline never sees an exception.
  * idempotent: the event carries unique_id "wa-optin-<lead_id>", so Klaviyo
    de-duplicates repeats; a local marker file also skips re-sends.
  * retries 429 / 5xx with backoff (honours Retry-After).
  * never logs the key or full phone numbers (last 4 only).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("klaviyo_sync")

API = "https://a.klaviyo.com/api"
REVISION = "2025-07-15"
STOCKIST_LIST = os.getenv("KLAVIYO_STOCKIST_LIST_ID", "Vvfcpj")
WHATSAPP_LIST = os.getenv("KLAVIYO_WHATSAPP_LIST_ID", "R3aC2j")
METRIC = "Qualified Lead WhatsApp Opt-In"
_BACKEND = Path(__file__).resolve().parents[2]
MARKER = _BACKEND / "data" / "klaviyo_wa_optin_synced.json"
_lock = threading.Lock()


def mask(phone) -> str:
    s = re.sub(r"\D", "", str(phone or ""))
    return ("…" + s[-4:]) if s else "-"


def to_e164(phone) -> str | None:
    """Indian-default E.164. Returns None when it cannot be made valid."""
    raw = str(phone or "").strip()
    d = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 10 <= len(d) <= 15:
        return "+" + d
    if d.startswith("00"):
        d = d[2:]
        return "+" + d if 10 <= len(d) <= 15 else None
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        return "+91" + d
    if len(d) == 12 and d.startswith("91"):
        return "+" + d
    return None


def _key() -> str | None:
    k = os.getenv("KLAVIYO_PRIVATE_KEY")
    if not k:
        try:
            from dotenv import dotenv_values
            k = dotenv_values(_BACKEND / ".env").get("KLAVIYO_PRIVATE_KEY")
        except Exception:
            k = None
    return k or None


def _request(method, path, body=None, *, session=None, attempts=4, sleep=time.sleep):
    import requests
    s = session or requests
    key = _key()
    if not key:
        raise RuntimeError("KLAVIYO_PRIVATE_KEY not configured")
    headers = {"Authorization": f"Klaviyo-API-Key {key}", "revision": REVISION,
               "accept": "application/vnd.api+json",
               "content-type": "application/vnd.api+json"}
    for i in range(attempts):
        try:
            r = s.request(method, API + path, headers=headers,
                          data=json.dumps(body) if body is not None else None, timeout=20)
        except Exception as e:  # network error: retry
            if i == attempts - 1:
                raise
            sleep(min(2 ** i, 8)); continue
        if r.status_code == 429 or r.status_code >= 500:
            if i == attempts - 1:
                raise RuntimeError(f"Klaviyo {path} -> {r.status_code} after {attempts} tries")
            ra = r.headers.get("Retry-After") if hasattr(r, "headers") else None
            sleep(min(float(ra) if ra and str(ra).replace('.', '', 1).isdigit() else 2 ** i, 30))
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Klaviyo {path} -> {r.status_code}: {r.text[:300]}")
        return r
    raise RuntimeError("unreachable")


def _profile_attrs(lead: dict, phone: str) -> dict:
    name = (lead.get("contact_name") or "").strip()
    first, _, last = name.partition(" ")
    a = {"phone_number": phone,
         "organization": lead.get("company") or None,
         "location": {"city": lead.get("city") or None, "country": "India"},
         "properties": {"lead_id": lead.get("id"), "segment": lead.get("segment"),
                        "company": lead.get("company"),
                        "source": "AI_CALL_WHATSAPP_OPT_IN"}}
    if first:
        a["first_name"] = first
    if last:
        a["last_name"] = last
    if lead.get("email"):
        a["email"] = lead["email"]
    return a


def _load_marker() -> dict:
    try:
        return json.loads(MARKER.read_text())
    except Exception:
        return {}


def _save_marker(m: dict) -> None:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(m, indent=1))


def sync_lead(lead: dict, *, session=None, sleep=time.sleep, lists=None,
              force=False, send_event=True) -> dict:
    # consent_call_ref / consent_at travel in the snapshot (28 Oct): the call's
    # own id and time, so Klaviyo's consent record says which call and when.
    """Synchronous sync. Returns a status dict; raises nothing."""
    lid = str(lead.get("id"))
    lists = list(lists) if lists is not None else [STOCKIST_LIST, WHATSAPP_LIST]
    phone = to_e164(lead.get("whatsapp_number") or lead.get("consent_phone") or lead.get("phone"))
    res = {"lead_id": lid, "phone": mask(phone), "ok": False, "steps": []}
    if not phone:
        res["error"] = "no valid E.164 phone"; log.warning("klaviyo_sync lead %s: %s", lid, res["error"]); return res
    if not _key():
        res["error"] = "no KLAVIYO_PRIVATE_KEY"; log.warning("klaviyo_sync lead %s: no key", lid); return res
    with _lock:
        if not force and lid in _load_marker():
            res.update(ok=True, skipped="already synced"); return res
    try:
        attrs = _profile_attrs(lead, phone)
        consented_at = _iso_past(lead.get("consent_at"))
        if lead.get("consent_call_ref"):
            attrs["properties"]["wa_consent_call_id"] = str(lead["consent_call_ref"])
        if consented_at:
            attrs["properties"]["wa_consented_at"] = consented_at
        attrs["properties"]["wa_consent_source"] = "AI_CALL"
        r = _request("POST", "/profile-import", {"data": {"type": "profile", "attributes": attrs}},
                     session=session, sleep=sleep)
        pid = r.json()["data"]["id"]; res["profile_id"] = pid; res["steps"].append("profile")
        # 29 Oct: ONE consent job with NO list relationship, then plain list
        # adds. The 28 Oct form (consent + list relationship per list) was
        # accepted (202) but never applied: 1043 and 550 stayed NEVER_SUBSCRIBED.
        wa = {"consent": "SUBSCRIBED"}
        job = {"custom_source": (f"AI call {lead['consent_call_ref']}" if lead.get("consent_call_ref")
                                 else "AI call WhatsApp opt-in")}
        if consented_at:
            # Consent was given on the call: import it with its real time.
            # historical_import also means Klaviyo sends NO double opt-in
            # confirmation message to the number.
            wa["consented_at"] = consented_at
            job["historical_import"] = True
        job["profiles"] = {"data": [{"type": "profile", "id": pid, "attributes": {
            "phone_number": phone,
            "subscriptions": {"whatsapp": {"marketing": wa}}}}]}
        try:
            _request("POST", "/profile-subscription-bulk-create-jobs",
                     {"data": {"type": "profile-subscription-bulk-create-job", "attributes": job}},
                     session=session, sleep=sleep)
            res["steps"].append("wa_consent")
        except Exception as e:  # consent may be refused (e.g. WA not set up); still list them
            err_msg = str(e).lower()
            if "after current subscription date" in err_msg or "already subscribed" in err_msg:
                log.info("klaviyo_sync lead %s already subscribed to WA marketing", lid)
                res["steps"].append("wa_already_subscribed")
            else:
                log.warning("klaviyo_sync lead %s consent failed: %s", lid, str(e)[:200])
                res["steps"].append("wa_consent_failed")
        for lst in lists:
            try:
                _request("POST", f"/lists/{lst}/relationships/profiles",
                         {"data": [{"type": "profile", "id": pid}]}, session=session, sleep=sleep)
                res["steps"].append(f"list:{lst}")
            except Exception as e:
                log.warning("klaviyo_sync lead %s list %s failed: %s", lid, lst, str(e)[:200])
                res["steps"].append(f"list_failed:{lst}")
        if send_event:
            ev = {"data": {"type": "event", "attributes": {
                "unique_id": f"wa-optin-{lid}",
                "properties": {"lead_id": lead.get("id"), "company": lead.get("company"),
                               "city": lead.get("city"), "segment": lead.get("segment"),
                               "source": "AI_CALL",
                               "call_id": lead.get("consent_call_ref"),
                               "consented_at": consented_at},
                "metric": {"data": {"type": "metric", "attributes": {"name": METRIC}}},
                "profile": {"data": {"type": "profile", "id": pid}}}}}
            _request("POST", "/events", ev, session=session, sleep=sleep)
            res["steps"].append("event")
        res["ok"] = True
        with _lock:
            m = _load_marker(); m[lid] = {"profile_id": pid, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            _save_marker(m)
        log.info("klaviyo_sync lead %s phone %s ok: %s", lid, mask(phone), res["steps"])
    except Exception as e:
        res["error"] = str(e)[:300]
        log.warning("klaviyo_sync lead %s phone %s failed: %s", lid, mask(phone), res["error"])
    return res


def _iso_past(value) -> str | None:
    """ISO-8601 UTC string for consented_at, only if it is a real past time."""
    from datetime import datetime, timezone
    if not value:
        return None
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)   # DB timestamps are naive UTC
        dt = dt.astimezone(timezone.utc)
        if dt >= datetime.now(timezone.utc):
            return None
        return dt.isoformat()
    except Exception:
        return None


def lead_snapshot(lead, call_ref=None, consented_at=None) -> dict:
    snap = {k: getattr(lead, k, None) for k in
            ("id", "company", "contact_name", "city", "segment", "email",
             "phone", "whatsapp_number", "consent_phone")}
    snap["consent_call_ref"] = call_ref
    snap["consent_at"] = consented_at or getattr(lead, "consent_timestamp", None)
    return snap


def schedule(lead, call_ref=None, consented_at=None) -> None:
    """Fire-and-forget from the outcome pipeline. Never raises."""
    try:
        if (os.getenv("KLAVIYO_WA_SYNC_ENABLED", "1") in ("0", "false", "False")
                or os.getenv("PYTEST_CURRENT_TEST")):  # never hit live Klaviyo from tests
            return
        snap = lead_snapshot(lead, call_ref=call_ref, consented_at=consented_at)
        threading.Thread(target=sync_lead, args=(snap,), daemon=True,
                         name=f"klaviyo-wa-{snap.get('id')}").start()
    except Exception as e:
        log.warning("klaviyo_sync schedule failed: %s", e)


def resolve_event_destination(props, profile_attrs):
    """Return (e164, evidence_label).

    To Number is the destination only when it is valid E.164. Klaviyo also
    puts a WhatsApp BSUID in that field; a BSUID is not a phone and is not
    truncated into one. The profile phone is accepted only when To Number is
    exactly that BSUID and the profile phone itself parses to E.164. The
    caller still has to match that phone to the stored lead.
    """
    raw = (props or {}).get("To Number")
    parsed = to_e164(raw)
    if parsed:
        return parsed, "event_to_number"
    raw_s = str(raw or "").strip()
    bsuid = str((props or {}).get("WhatsApp BSUID") or "").strip()
    if raw_s and bsuid and raw_s == bsuid:
        profile_phone = to_e164((profile_attrs or {}).get("phone_number"))
        if profile_phone:
            return profile_phone, "profile_phone_for_bsuid"
        return None, "bsuid_without_profile_phone"
    if not raw_s:
        return None, "missing_destination_number"
    return None, "destination_not_e164"


def sync_klaviyo_delivery_events(db=None) -> dict:
    """Reconcile B2B WhatsApp relays and failures using exact provider evidence.

    Relayed WhatsApp means sent to the WhatsApp provider, not delivered/read.
    Failed-to-deliver events take precedence over relays for the same send.
    Joins require B2B flow + profile external_id + exact phone + nearest prior
    local send. A WhatsApp BSUID in To Number is not a phone; the profile
    phone is used only when it is valid E.164 and exactly matches the lead.
    Unmatched events are quarantined without storing full numbers.
    """
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urlencode
    from sqlalchemy import text
    if db is None:
        from app.database.database import SessionLocal
        db = SessionLocal()
        close = True
    else:
        close = False
    try:
        from app.models.models import OutboundWhatsApp, B2BLead
        db.execute(text("""CREATE TABLE IF NOT EXISTS klaviyo_delivery_quarantine (
            provider_event_id TEXT PRIMARY KEY, flow_id TEXT, event_type TEXT,
            event_at TEXT, profile_external_id TEXT, destination_last4 TEXT,
            vendor_error_code TEXT, failure_type TEXT, reason TEXT,
            recorded_at TEXT NOT NULL, resolution_status TEXT NOT NULL DEFAULT 'OPEN'
        )"""))
        rows = db.query(OutboundWhatsApp).filter(
            OutboundWhatsApp.status.in_(["QUEUED_TO_KLAVIYO", "FAILED", "SENT"])
        ).all()
        if not rows:
            db.commit()
            return {"checked": 0, "failed_updated": 0, "relayed_updated": 0,
                    "pending_provider_reconciliation": 0, "quarantined_new": 0}
        since = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
        flow_ids = {"TUrAGc", "TbKyue"}
        def fetch(metric_id):
            filt = f'and(equals(metric_id,"{metric_id}"),greater-than(datetime,{since}))'
            path = "/events?" + urlencode({"filter": filt, "include": "profile",
                "page[size]": "1000", "sort": "-datetime"})
            payload = _request("GET", path).json()
            profiles = {x.get("id"): x.get("attributes") or {} for x in payload.get("included", [])
                        if x.get("type") == "profile"}
            return payload.get("data", []), profiles
        failed_events, failed_profiles = fetch("UtYn7N")
        relayed_events, relayed_profiles = fetch("VJMsMV")
        counts = {"failed_updated":0,"failed_enriched":0,"relayed_updated":0,
                  "already_reconciled":0,"failure_precedence":0,"unmatched":0,"ambiguous":0,
                  "quarantined_new":0,"permanently_unmatched":0}
        def quarantine(event, flow_id, event_type, event_at, profile_id, props, reason):
            event_id = str(event.get("id") or "")
            if not event_id:
                return
            result = db.execute(text("""INSERT OR IGNORE INTO klaviyo_delivery_quarantine
                (provider_event_id,flow_id,event_type,event_at,profile_external_id,destination_last4,
                 vendor_error_code,failure_type,reason,recorded_at)
                VALUES(:id,:flow,:etype,:at,:profile,:last4,:code,:failure,:reason,:now)"""), {
                "id":event_id,"flow":flow_id,"etype":event_type,"at":event_at,
                "profile":profile_id,"last4":str(props.get("To Number") or "")[-4:],
                "code":str(props.get("Vendor Error Code") or "")[:32],
                "failure":str(props.get("Failure Type") or "")[:180],"reason":reason,
                "now":datetime.now(timezone.utc).isoformat()})
            if result.rowcount:
                counts["quarantined_new"] += 1
            else:
                # Keep an open quarantine, but correct a stale reason when a
                # later exact read can name the blocker. Do not resolve it.
                db.execute(text("""UPDATE klaviyo_delivery_quarantine
                    SET reason=:reason
                    WHERE provider_event_id=:id AND resolution_status='OPEN'
                      AND COALESCE(reason,'')<>:reason"""),
                    {"id": event_id, "reason": reason})
        def resolve_quarantine(event_id):
            if not event_id:
                return
            db.execute(text("""UPDATE klaviyo_delivery_quarantine
                SET resolution_status='RESOLVED_EXACT_MATCH'
                WHERE provider_event_id=:id AND resolution_status='OPEN'"""),
                {"id": str(event_id)})
        def process(event, profiles, kind):
            attrs = event.get("attributes") or {}
            props = attrs.get("event_properties") or {}
            flow_id = props.get("$flow")
            if flow_id not in flow_ids:
                return
            if kind == "FAILED" and props.get("Failure Source") != "undelivered":
                return
            event_id = str(event.get("id") or "")
            if event_id:
                locked = db.execute(text(
                    "SELECT resolution_status FROM klaviyo_delivery_quarantine WHERE provider_event_id=:id"
                ), {"id": event_id}).fetchone()
                if locked and str(locked[0]) == "PERMANENTLY_UNMATCHED":
                    counts["permanently_unmatched"] += 1
                    return
            rel = (event.get("relationships") or {}).get("profile") or {}
            profile = rel.get("data") or {}
            profile_attrs = profile.get("attributes") or profiles.get(profile.get("id"), {})
            external_id = str(profile_attrs.get("external_id") or "")
            to_number, destination_evidence = resolve_event_destination(props, profile_attrs)
            if not to_number:
                counts["unmatched"] += 1
                quarantine(event,flow_id,kind,attrs.get("datetime"),external_id or None,props,destination_evidence)
                return
            if external_id.startswith("lead-"):
                try:
                    lead_id = int(external_id[5:])
                except ValueError:
                    counts["unmatched"] += 1
                    quarantine(event,flow_id,kind,attrs.get("datetime"),external_id,props,"invalid_profile_external_id")
                    return
                lead = db.get(B2BLead, lead_id)
                if not lead:
                    counts["unmatched"] += 1
                    quarantine(event,flow_id,kind,attrs.get("datetime"),external_id,props,"no_local_lead")
                    return
            else:
                # A missing Klaviyo external_id can be recovered only when the
                # exact E.164 destination maps to one and only one local lead.
                phone_matches=[]
                for candidate_lead in db.query(B2BLead).all():
                    numbers={to_e164(getattr(candidate_lead,key,None)) for key in ("consent_phone","whatsapp_number","phone")}
                    if to_number in numbers:
                        phone_matches.append(candidate_lead)
                if len(phone_matches) != 1:
                    counts["unmatched"] += 1
                    quarantine(event,flow_id,kind,attrs.get("datetime"),None,props,
                               "no_unique_local_lead_for_destination" if not phone_matches else "ambiguous_local_lead_for_destination")
                    return
                lead=phone_matches[0]
                lead_id=lead.id
            try:
                event_at = datetime.fromisoformat(str(attrs.get("datetime") or "").replace("Z", "+00:00"))
                if event_at.tzinfo is None: event_at = event_at.replace(tzinfo=timezone.utc)
                event_at = event_at.astimezone(timezone.utc)
            except Exception:
                counts["unmatched"] += 1
                quarantine(event,flow_id,kind,None,external_id,props,"invalid_provider_timestamp")
                return
            valid_numbers = {to_e164(getattr(lead,key,None)) for key in ("consent_phone","whatsapp_number","phone")}
            valid_numbers.discard(None)
            if to_number not in valid_numbers:
                counts["unmatched"] += 1
                quarantine(event,flow_id,kind,event_at.isoformat(),external_id,props,"destination_number_mismatch")
                return
            if kind == "RELAYED":
                for failed_row in rows:
                    if failed_row.lead_id != lead_id or failed_row.status != "FAILED" or not failed_row.failed_at:
                        continue
                    failed_at = failed_row.failed_at
                    if failed_at.tzinfo is None: failed_at = failed_at.replace(tzinfo=timezone.utc)
                    else: failed_at = failed_at.astimezone(timezone.utc)
                    if abs(event_at - failed_at) <= timedelta(minutes=10):
                        counts["failure_precedence"] += 1
                        return
            candidates=[]
            for row in rows:
                if str(row.status or "").startswith("BLOCKED_"):
                    continue
                if row.lead_id != lead_id or row.status not in ("QUEUED_TO_KLAVIYO","FAILED","SENT") or not row.created_at:
                    continue
                try: prior=json.loads(row.whatsapp_response or "{}")
                except Exception: prior={}
                if prior.get("provider_event_id") and prior.get("provider_event_id") != event.get("id"):
                    continue
                created=row.created_at
                if created.tzinfo is None: created=created.replace(tzinfo=timezone.utc)
                else: created=created.astimezone(timezone.utc)
                age=event_at-created
                if timedelta(0) <= age <= timedelta(hours=24): candidates.append((age,row,prior))
            candidates.sort(key=lambda x:x[0])
            if not candidates:
                counts["unmatched"] += 1
                quarantine(event,flow_id,kind,event_at.isoformat(),external_id,props,"no_local_send_in_24h_window")
                return
            if len(candidates)>1 and (candidates[1][0]-candidates[0][0])<timedelta(seconds=60):
                counts["ambiguous"] += 1
                quarantine(event,flow_id,kind,event_at.isoformat(),external_id,props,"ambiguous_multiple_local_sends")
                return
            row,prior=candidates[0][1],candidates[0][2]
            if prior.get("provider_event_id")==event.get("id"):
                counts["already_reconciled"] += 1
                resolve_quarantine(event.get("id"))
                return
            if kind == "RELAYED" and row.status == "FAILED":
                # Provider failure is final even if a relay event also exists.
                counts["failure_precedence"] += 1
                return
            if kind == "FAILED":
                row.whatsapp_response=json.dumps({"provider":"klaviyo","provider_event":"Failed to Deliver WhatsApp",
                    "provider_event_id":event.get("id"),"flow_id":flow_id,"flow_message_id":props.get("$message"),
                    "destination_evidence":destination_evidence,
                    "vendor_error_code":props.get("Vendor Error Code"),"failure_type":props.get("Failure Type"),
                    "failure_source":props.get("Failure Source"),"prior_response":row.whatsapp_response},ensure_ascii=False)
                if row.status != "FAILED":
                    row.status="FAILED"; counts["failed_updated"] += 1
                else: counts["failed_enriched"] += 1
                if row.failed_at is None: row.failed_at=event_at.replace(tzinfo=None)
                resolve_quarantine(event.get("id"))
            else:
                row.whatsapp_response=json.dumps({"provider":"klaviyo","provider_event":"Relayed WhatsApp",
                    "provider_event_id":event.get("id"),"flow_id":flow_id,"flow_message_id":props.get("$message"),
                    "destination_evidence":destination_evidence,
                    "provider_relayed":True,"delivery_confirmed":False,"prior_response":row.whatsapp_response},ensure_ascii=False)
                if row.status == "QUEUED_TO_KLAVIYO":
                    row.status="SENT"; row.sent_at=event_at.replace(tzinfo=None); counts["relayed_updated"] += 1
                elif row.status == "SENT": counts["already_reconciled"] += 1
                resolve_quarantine(event.get("id"))
        for e in failed_events: process(e,failed_profiles,"FAILED")
        for e in relayed_events: process(e,relayed_profiles,"RELAYED")
        db.commit()
        pending=db.query(OutboundWhatsApp).filter(OutboundWhatsApp.status=="QUEUED_TO_KLAVIYO").count()
        return {"checked":len(failed_events)+len(relayed_events),**counts,
                "pending_provider_reconciliation":pending}
    finally:
        if close: db.close()
