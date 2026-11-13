"""3 Nov 2026 fix 3 (Hiten 11:49 IST: "check unproven 6-series numbers before
dialing").

A scraped +91 6xxxxxxxxx mobile number that has never connected is held out of
the AI dialer until a FREE check proves it. Free proofs, in order:
  * phone_verified / phone_verified_at / whatsapp_verified on the lead,
  * phone_trust == VERIFIED (founder or inbound confirmed),
  * the number has rung or been answered before (this lead or any lead with
    the same phone string) - same evidence rule as never_rang.number_has_rung.
No paid lookup and no test dial is used. The series is NOT blanket-blocked:
proven 6-series numbers dial normally, and non-scraped (inbound / manual)
numbers are not held. Email / WhatsApp eligibility is untouched: this only
answers "may the AI dialer ring this number now".

Rollback lever: AI_CALL_HOLD_UNPROVEN_6_SERIES=0 (then nothing is held).
Release one lead: set phone_verified=1 (or phone_trust='VERIFIED') after a
free manual check.
"""
from __future__ import annotations

import os
import re

ENV = "AI_CALL_HOLD_UNPROVEN_6_SERIES"
HOLD_PREFIX = "UNPROVEN_6_SERIES_HOLD"
SCRAPE_MARKERS = ("perplexity", "brave", "indiamart", "tradeindia", "google", "websearch",
                  "web search", "justdial", "scrape", "serp", "places", "search", "directory")


def enabled() -> bool:
    return (os.getenv(ENV, "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def national(phone) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d


def is_six_series_mobile(phone) -> bool:
    n = national(phone)
    return len(n) == 10 and n[0] == "6"


def is_scraped(lead) -> bool:
    # The phone's own source decides when it is recorded (lead_source defaults
    # to "Google Maps" for every row, so it only counts when phone_source is empty).
    src = (getattr(lead, "phone_source", None) or "").strip()
    if not src:
        src = " ".join(str(getattr(lead, a, "") or "") for a in ("lead_source", "acquisition_source"))
    src = src.lower()
    return any(m in src for m in SCRAPE_MARKERS)


def free_proof(db, lead) -> str | None:
    """Return why the number counts as proven (free evidence), else None."""
    if getattr(lead, "phone_verified", None):
        return "phone_verified"
    if getattr(lead, "phone_verified_at", None):
        return "phone_verified_at"
    if getattr(lead, "whatsapp_verified", None):
        return "whatsapp_verified"
    if (getattr(lead, "phone_trust", None) or "").upper() == "VERIFIED":
        return "phone_trust=VERIFIED"
    if db is None:
        return None
    from types import SimpleNamespace
    from app.services.call_intelligence.never_rang import number_has_rung
    if number_has_rung(db, lead):
        return "rang before"
    try:
        from app.models.models import B2BLead
        others = (db.query(B2BLead.id)
                    .filter(B2BLead.phone == lead.phone, B2BLead.id != lead.id).all())
    except Exception:  # noqa: BLE001
        others = []
    for (oid,) in others:
        if number_has_rung(db, SimpleNamespace(id=oid)):
            return "rang before (same number on lead %s)" % oid
    return None


def hold_reason(db, lead) -> str | None:
    """None = may dial. A string = held (reason), never raises for bad data."""
    if not enabled() or lead is None:
        return None
    if not is_six_series_mobile(getattr(lead, "phone", None)):
        return None
    if not is_scraped(lead):
        return None
    if db is None:
        try:
            from sqlalchemy.orm import object_session
            db = object_session(lead)
        except Exception:  # noqa: BLE001
            db = None
        if db is None:
            # No session: only the lead's own flags can prove it; the queue
            # selector and prepare_dial (which have a session) do the full check.
            return None
    if free_proof(db, lead):
        return None
    return (f"{HOLD_PREFIX}: scraped +91 6-series number, never connected and not verified - "
            "held from the AI dialer until a free check proves it (email unaffected)")
