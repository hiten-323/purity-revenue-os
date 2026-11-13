"""Address-level bounce / suppression guard (added 28 Oct 2026).

Why this exists: info@gloryent.biz (lead 386) hard-bounced on 21 and 24 Oct
(550 no such recipient). The bounce was recorded only in the box CRM
(contacts_master.csv); nothing on this server ingests mailer-daemon bounces,
so the lead stayed email_trust=VERIFIED / contact_status=CONTACTABLE (SMTP
acceptance had even promoted it) and the sequence auto-approved another
follow-up on 28 Oct. Hiten's rule: a bounced address is never retried.

check(addr) answers one question for any outbound address, with or without a
lead_id: is this address marked bounced or suppressed anywhere we know of?

  1. EMAIL_SUPPRESSION_FILE (default /opt/purity-revenue-os/data/
     email_suppression.txt): "address<TAB>reason" lines synced from the box
     CRM by tools/export_suppression.py.
  2. Server data, for every lead carrying this address:
       email_trust = BOUNCED
       contact_status in BOUNCED / OPTED_OUT / DO_NOT_CONTACT
       an EMAIL_BOUNCED / HARD_BOUNCE event addressed to it (or with no "to")
       an UNSUBSCRIBED / DO_NOT_CONTACT / COMPLAINT event on that lead
  3. email_drafts rows to this address with delivery_status = BOUNCED.

Fails closed: a missing/unreadable suppression file or a DB error raises, and
the caller turns that into a HELD (no send).
"""
from __future__ import annotations

import json
import os

SUPPRESSION_FILE = os.getenv(
    "EMAIL_SUPPRESSION_FILE", "/opt/purity-revenue-os/data/email_suppression.txt")
STOP_TRUST = {"BOUNCED"}           # INVALID/PURGED stay per-lead in trust_promoter.may_send
STOP_STATUS = {"BOUNCED", "OPTED_OUT", "DO_NOT_CONTACT"}
BOUNCE_EVENTS = ("EMAIL_BOUNCED", "HARD_BOUNCE")
LEAD_STOP_EVENTS = ("UNSUBSCRIBED", "DO_NOT_CONTACT", "COMPLAINT")

_cache: dict = {"mtime": None, "data": {}}


def _norm(addr) -> str:
    return (addr or "").strip().lower()


def load_suppression_file(path: str | None = None) -> dict:
    """{address: reason}. Raises if the file is missing (fail closed)."""
    path = path or SUPPRESSION_FILE
    mtime = os.stat(path).st_mtime          # FileNotFoundError -> caller HOLDs
    if _cache["mtime"] == mtime and _cache.get("path") == path:
        return _cache["data"]
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            addr, _, why = line.partition("\t")
            addr = _norm(addr)
            if "@" in addr:
                data[addr] = why.strip() or "on suppression list"
    _cache.update(mtime=mtime, data=data, path=path)
    return data


def _payload(p) -> dict:
    if isinstance(p, dict):
        return p
    try:
        return json.loads(p or "{}") or {}
    except Exception:
        return {}


def check(to_email, db=None) -> tuple[bool, str]:
    """(True, "not suppressed") or (False, reason). Raises on lookup failure."""
    addr = _norm(to_email)
    if not addr:
        return True, "no address"

    listed = load_suppression_file()
    if addr in listed:
        return False, f"{addr} is suppressed ({listed[addr]})"

    own = db is None
    if own:
        try:
            from app.database.database import SessionLocal
            db = SessionLocal()
        except Exception:
            db = None
    if db is None or not hasattr(db, "execute"):
        return True, "not suppressed"
    try:
        from sqlalchemy import text
        try:
            leads = db.execute(text(
                "SELECT id, email_trust, contact_status FROM b2b_leads "
                "WHERE lower(trim(email)) = :a"), {"a": addr}).fetchall()
        except Exception as e:
            if "no such table" in str(e).lower():
                leads = []
            else:
                raise
        ids = []
        for lid, trust, cstatus in leads:
            ids.append(lid)
            if (trust or "").upper() in STOP_TRUST:
                return False, f"{addr} is marked {trust.upper()} (lead {lid})"
            if (cstatus or "").upper() in STOP_STATUS:
                return False, f"{addr} contact_status={cstatus.upper()} (lead {lid})"
        if ids:
            marks = ",".join(str(int(i)) for i in ids)
            try:
                rows = db.execute(text(
                    "SELECT lead_id, event_type, payload FROM workflow_events "
                    f"WHERE lead_id IN ({marks}) AND event_type IN "
                    "('EMAIL_BOUNCED','HARD_BOUNCE','UNSUBSCRIBED','DO_NOT_CONTACT','COMPLAINT')"
                )).fetchall()
            except Exception as e:
                if "no such table" in str(e).lower():
                    rows = []
                else:
                    raise
            for lid, et, p in rows:
                if et in LEAD_STOP_EVENTS:
                    return False, f"{addr}: lead {lid} has {et}"
                to = _norm(_payload(p).get("to"))
                if not to or to == addr:
                    return False, f"{addr} bounced before ({et}, lead {lid})"
        try:
            b = db.execute(text(
                "SELECT id FROM email_drafts WHERE lower(trim(recipient)) = :a "
                "AND upper(delivery_status) = 'BOUNCED' LIMIT 1"), {"a": addr}).fetchone()
            if b is not None:
                return False, f"{addr} bounced before (email_drafts {b[0]})"
        except Exception as e:
            if "no such table" in str(e).lower():
                pass
            else:
                raise
        return True, "not suppressed"
    finally:
        if own:
            db.close()
