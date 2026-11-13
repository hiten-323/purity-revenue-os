"""Founder rule (2026-10-07): a shared / chain email address receives at most ONE
outreach sequence, ever, across all branches.

Why: one inbox listed on several businesses (a chain head office, a group owner,
an agency) is ONE relationship. Emailing it once per branch is how a chain marks
us as spam. The old approach demoted every lead sharing the address out of
sendable trust, and the trust sweep promoted them back, so leads flip-flopped
every cycle. Now trust is left alone; the rule is enforced at send time.

Representative lead for an address:
  1. the first lead ever emailed at that address (EMAIL_SENT ledger), fixed
     forever once it exists; otherwise
  2. the highest-fit sendable lead (final_score, then coffee_buying_score,
     then lowest id).
Every other lead holding the address is skipped for EMAIL only. Calls are not
affected. Reversible: EMAIL_SHARED_ADDRESS_RULE=0 switches the rule off; nothing
is deleted and no trust is changed.
"""
from __future__ import annotations

import os

INACTIVE_STATUSES = ("DISQUALIFIED", "CLOSED_LOST", "DO_NOT_CONTACT")


def enabled() -> bool:
    return (os.getenv("EMAIL_SHARED_ADDRESS_RULE", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def _norm(addr) -> str:
    return (addr or "").strip().lower()


def first_emailed_lead(db, addr: str):
    from sqlalchemy import text
    row = db.execute(text(
        "SELECT lead_id FROM workflow_events "
        "WHERE event_type = 'EMAIL_SENT' AND lead_id IS NOT NULL "
        "AND lower(trim(CASE WHEN json_valid(payload) THEN json_extract(payload, '$.to') END)) = :a "
        "ORDER BY occurred_at ASC, id ASC LIMIT 1"), {"a": addr}).first()
    return row[0] if row else None


def _eligible(lead) -> bool:
    from app.services.email_repeat_guard import hold_ids
    from app.services.trust_promoter import may_send
    if lead.id in hold_ids():
        return False
    if (lead.status or "").upper() in INACTIVE_STATUSES:
        return False
    try:
        return bool(may_send(lead)[0])
    except Exception:
        return False


def _pick(db, addr: str, leads: list):
    fe = first_emailed_lead(db, addr)
    if fe is not None:
        return fe, "first lead emailed at this address"
    pool = [l for l in leads if _eligible(l)] or list(leads)
    best = max(pool, key=lambda l: (l.final_score or 0, l.coffee_buying_score or 0, -l.id))
    return best.id, "highest-fit sendable lead (no one emailed at this address yet)"


def representative(db, addr: str):
    """(representative_lead_id, [lead ids holding addr], why)."""
    from sqlalchemy import func
    from app.models.models import B2BLead
    addr = _norm(addr)
    leads = db.query(B2BLead).filter(func.lower(func.trim(B2BLead.email)) == addr).all()
    ids = sorted(l.id for l in leads)
    if len(leads) < 2:
        return (ids[0] if ids else None), ids, "address not shared"
    rep, why = _pick(db, addr, leads)
    return rep, ids, why


def check(db, lead_id: int, to_email: str | None = None) -> tuple[bool, str]:
    """(may_send, why). Refusals start with 'BLOCKED: SHARED_ADDRESS'."""
    if not enabled():
        return True, "shared-address rule disabled"
    addr = _norm(to_email)
    if not addr:
        from app.models.models import B2BLead
        l = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        addr = _norm(getattr(l, "email", ""))
    if not addr:
        return True, "no address"
    rep, ids, why = representative(db, addr)
    if len(ids) < 2 or rep is None or rep == lead_id:
        return True, "representative" if len(ids) >= 2 else "address not shared"
    return False, (f"BLOCKED: SHARED_ADDRESS one-outreach-per-address rule - {addr} is on "
                   f"{len(ids)} leads; email goes only to representative lead {rep} ({why}). "
                   f"Calls unaffected.")


def non_representatives(db) -> dict:
    """{lead_id: (addr, representative_id, why)} for every lead skipped by the rule."""
    if not enabled():
        return {}
    from collections import defaultdict
    from app.models.models import B2BLead
    groups = defaultdict(list)
    for l in db.query(B2BLead).filter(B2BLead.email.isnot(None), B2BLead.email != "").all():
        groups[_norm(l.email)].append(l)
    out = {}
    for addr, leads in groups.items():
        if len(leads) < 2:
            continue
        rep, why = _pick(db, addr, leads)
        for l in leads:
            if l.id != rep:
                out[l.id] = (addr, rep, why)
    return out
