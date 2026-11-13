"""Import-time phone check for b2b_leads (27 Oct 2026, Hiten via architect).

WHY
The dead +91 6060/6106/6144/6968 "mobiles" were never published by the
businesses. They are 10-digit windows cut out of Facebook page / profile ids
(facebook.com/106106944413135 -> "6106944413"): the Brave Search blob in
contact_enricher includes each result's URL, and identity.PHONE_RE had no digit
boundary, so the auto-warm enrichment (purity-worker -> _silent_verify_and_enrich)
wrote them onto leads 153, 259, 682, 2199 and 2219. Toll-free 1800 numbers and
malformed numbers reached the table the same way, with nothing to stop them
before the calling queue.

WHAT
Every write of b2b_leads.phone (insert or update, any path: API, worker, bot,
script) is classified with the SAME rule as the pre-dial filter
(founder_call_pipeline.predial_block, mirror of predial_filter.js):

    dead series                  -> phone_trust = "DEAD_SERIES"
    toll-free / premium / malformed / not an Indian mobile or landline
                                 -> phone_trust = "INVALID"
    valid mobile or geographic landline -> untouched

Both states are excluded from the AI call queue. do_not_call is NEVER set, so
email (outreach_orchestrator.stop_reason) and WhatsApp are unaffected. The
number itself is kept, so nothing is lost and the flag is reversible.

When a flagged number is later replaced by a valid one, the flag this rule set
for the OLD number is lifted (back to DISCOVERED), so a corrected number is not
blocked forever. Carrier evidence (SIP_INVALID) and SHARED_QUARANTINE are never
lifted here.

The guard runs as a mapper before_insert / before_update listener, so the order
of assignments in a caller (e.g. phone then phone_trust="DISCOVERED") cannot
undo it. A failure inside the guard is logged and the write goes through.
"""
from __future__ import annotations

import logging

log = logging.getLogger("phone_intake")

FLAG_STATES = ("INVALID", "DEAD_SERIES")
# 27 Oct (Hiten): phone cleared because it was wrong / belonged to another lead.
# Lifted to DISCOVERED as soon as a dialable replacement number is written.
NEEDS_NEW_NUMBER = "NEEDS_NEW_NUMBER"
_UNSET = object()


def classify(phone):
    """None when empty or dialable, else (phone_trust, reason)."""
    if not (phone or "").strip():
        return None
    from app.services.founder_call_pipeline import predial_block
    from app.services import identity
    code = predial_block(phone)
    if not code:
        return None
    if code == "DEAD_SERIES":
        return ("DEAD_SERIES", "dead +91 6060/6106/6144/6968 series (Plivo #29655)")
    if identity.is_non_geographic(phone):
        return ("INVALID", "toll-free/premium 1800/1860/1900 - not callable")
    return ("INVALID", "malformed or not an Indian mobile/landline")


def apply(lead, old_phone=_UNSET):
    """Flag (or un-flag) lead.phone_trust from lead.phone. Returns the flag or None."""
    res = classify(getattr(lead, "phone", None))
    cur = (getattr(lead, "phone_trust", None) or "").upper()
    if res:
        trust, why = res
        if cur != trust:
            lead.phone_trust = trust
            log.warning("phone_intake: lead %s phone %r -> phone_trust %s (%s; was %s)",
                        getattr(lead, "id", None), lead.phone, trust, why, cur or None)
        return trust
    if cur in FLAG_STATES and old_phone is not _UNSET and classify(old_phone):
        lead.phone_trust = "DISCOVERED"
        log.warning("phone_intake: lead %s phone replaced by a dialable number; "
                    "lifted %s set for the old number", getattr(lead, "id", None), cur)
    if (cur == NEEDS_NEW_NUMBER and old_phone is not _UNSET
            and (getattr(lead, "phone", None) or "").strip()):
        lead.phone_trust = "DISCOVERED"
        log.warning("phone_intake: lead %s got a new dialable number; lifted %s",
                    getattr(lead, "id", None), cur)
    return None


def _before_insert(mapper, connection, target):
    try:
        apply(target)
    except Exception as e:  # never break an unrelated write
        log.error("phone_intake guard failed on insert: %s: %s", e.__class__.__name__, e)


def _before_update(mapper, connection, target):
    try:
        from sqlalchemy import inspect
        st = inspect(target)
        ph = st.attrs.phone.history
        tr = st.attrs.phone_trust.history
        if not (ph.has_changes() or tr.has_changes()):
            return
        # phone changed but its old value was not loaded (expired after commit) -> None:
        # still "phone changed", just no known old number.
        old = (ph.deleted[0] if ph.deleted else None) if ph.has_changes() else _UNSET
        apply(target, old)
    except Exception as e:
        log.error("phone_intake guard failed on update: %s: %s", e.__class__.__name__, e)


def register(cls=None) -> None:
    """Attach the guard to B2BLead. Idempotent; safe to call from any script."""
    from sqlalchemy import event
    if cls is None:
        from app.models.models import B2BLead as cls
    if not event.contains(cls, "before_insert", _before_insert):
        event.listen(cls, "before_insert", _before_insert)
    if not event.contains(cls, "before_update", _before_update):
        event.listen(cls, "before_update", _before_update)
