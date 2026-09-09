r"""
Every step a business goes through, on the business's own record.

Each lead should be able to answer, from its own row: what did we try, how, and
what did we conclude. Until now only founder calls could -- 19 rows in
lead_interactions, all method='founder_call'. Everything else the system did to
a business (enriched it, promoted its trust, refused to email it, skipped it
for WhatsApp) left nothing on the business itself. The knowledge lived in a
sweep's return value and then stopped existing.

lead_interactions was already the right home. It is described in models.py as
"the permanent, append-only record of every interaction with a business", and
it already carries the three fields this needs:

    method   how the step happened      email | whatsapp | phone | enrichment
                                        | trust | orchestrator | import | audit
    outcome  the conclusion, from a fixed vocabulary so it can be counted
    remark   why, in the words of the code that decided

The remark is passed through verbatim. It is never rewritten to read better --
a journal that improves on what the gate actually said will be believed, and
then trusted, and then wrong.

What gets a row, and what does not
----------------------------------
A row means something HAPPENED: a state changed, an action was taken, or an
action was blocked at the moment it was attempted.

Re-evaluating a lead and reaching the same conclusion is not an event. The
orchestrator checks four channels against 1,858 businesses; recording every
check would add 7,432 rows per sweep and bury the 19 real founder calls inside
a week. Those evaluations go to the decision log, which is built for volume
(scripts/decisions.py). This table is built for history.

_same_as_last() enforces that: an identical (method, outcome, remark) following
the same one is dropped. So a nightly sweep that changes nothing writes
nothing, and the day the answer changes, the journal shows exactly that day.
"""
from __future__ import annotations

from datetime import datetime

# How the step happened.
EMAIL = "email"
WHATSAPP = "whatsapp"
PHONE = "phone"
ENRICHMENT = "enrichment"
TRUST = "trust"
ORCHESTRATOR = "orchestrator"
IMPORT = "import"
AUDIT = "audit"
DISCOVERY = "discovery"

METHODS = (EMAIL, WHATSAPP, PHONE, ENRICHMENT, TRUST, ORCHESTRATOR,
           IMPORT, AUDIT, DISCOVERY, "founder_call")

# The conclusion. Fixed so it can be counted; the nuance lives in the remark.
SENT = "SENT"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"
UPDATED = "UPDATED"
PROMOTED = "PROMOTED"
DEMOTED = "DEMOTED"
QUEUED = "QUEUED"
NO_CHANNEL = "NO_CHANNEL"
CORRECTED = "CORRECTED"

# An OUTCOME is something that HAPPENED to the business: a message left the
# server, a send was refused at the moment it was attempted, trust actually
# moved, a call connected and the person said something.
#
# An ASSESSMENT is what the system concluded or intends: this lead is queued,
# no channel can reach it, this channel was skipped. Nothing has happened to
# the business.
#
# The two must not be counted together. A report showing "1,858 steps recorded"
# that is really 1,858 assessments and zero outcomes describes a system that
# has done nothing, in the language of a system that has done a great deal.
# That is the specific way this codebase has misled before.
OUTCOMES = frozenset({
    SENT, BLOCKED, PROMOTED, DEMOTED, UPDATED, CORRECTED,
    # AI and founder call results — the business actually spoke, or did not
    "INTERESTED", "NOT_INTERESTED", "NO_ANSWER", "WRONG_NUMBER", "OPT_OUT",
    "MEETING_REQUESTED", "SAMPLE_REQUESTED", "PRICE_OBJECTION",
    "EXISTING_SUPPLIER", "SEND_DETAILS", "SEND_PRICING", "SEND_WHATSAPP",
    "DO_NOT_CONTACT", "WRONG_PERSON", "BUSY", "GATEKEEPER", "CALL_LATER",
})

ASSESSMENTS = frozenset({QUEUED, NO_CHANNEL, SKIPPED})


def is_outcome(outcome: str) -> bool:
    """Did something happen to the business, or did we merely conclude something?"""
    return (outcome or "").strip().upper() in OUTCOMES


def _same_as_last(db, lead_id: int, method: str, outcome: str, remark: str) -> bool:
    """Has this exact conclusion already been recorded, most recently?"""
    from app.models.models import LeadInteraction
    last = (db.query(LeadInteraction)
              .filter(LeadInteraction.lead_id == lead_id)
              .order_by(LeadInteraction.id.desc())
              .first())
    return bool(last and last.method == method and last.outcome == outcome
                and (last.remark or "") == remark)


def record(lead, db, *, method: str, outcome: str, remark: str,
           by: str = "SYSTEM", force: bool = False, commit: bool = False):
    """Append one step to a business's own history. Returns the row, or None.

    None means the identical conclusion was already the most recent entry, so
    nothing changed and nothing was written. `force=True` records it anyway,
    for the cases where a repeat genuinely is the news (a second delivery
    failure to the same address is not the same fact as the first).

    Never raises. A journal that can break the step it is describing is worse
    than no journal.
    """
    from app.models.models import LeadInteraction

    try:
        lead_id = getattr(lead, "id", lead)
        if lead_id is None:
            return None
        remark = (remark or "").strip()[:1000]
        method = (method or "").strip().lower()
        outcome = (outcome or "").strip().upper()

        if not force and _same_as_last(db, lead_id, method, outcome, remark):
            return None

        row = LeadInteraction(
            lead_id=lead_id,
            occurred_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            created_by=by,
            method=method,
            outcome=outcome,
            remark=remark,
        )
        db.add(row)
        if commit:
            db.commit()
        return row
    except Exception as exc:  # noqa: BLE001
        try:
            import logging
            logging.getLogger(__name__).warning(
                "lead journal write failed for lead=%s: %s: %s",
                getattr(lead, "id", lead), exc.__class__.__name__, exc)
        except Exception:
            pass
        return None


def history(db, lead_id: int, limit: int = 200) -> list:
    """One business's complete story, oldest first — the order it happened in."""
    from app.models.models import LeadInteraction
    rows = (db.query(LeadInteraction)
              .filter(LeadInteraction.lead_id == lead_id)
              .order_by(LeadInteraction.occurred_at.asc(),
                        LeadInteraction.id.asc())
              .limit(limit).all())
    return [{
        "at": r.occurred_at,
        "by": r.created_by or "SYSTEM",
        "method": r.method or "",
        "outcome": r.outcome or "",
        "remark": r.remark or "",
        # Fields only a founder call fills in. Shown when present, never
        # defaulted — "no supplier recorded" and "they have no supplier" are
        # different facts.
        "decision_maker": r.decision_maker,
        "current_supplier": r.current_supplier or r.current_brand,
        "monthly_kg": r.monthly_consumption_kg,
        "next_followup": r.next_followup_date,
    } for r in rows]


def summary(db) -> dict:
    """How much of the estate has any recorded history at all."""
    from collections import Counter
    from app.models.models import B2BLead, LeadInteraction

    total = db.query(B2BLead).count()
    rows = db.query(LeadInteraction.lead_id, LeadInteraction.method,
                    LeadInteraction.outcome).all()
    by_method = Counter(m or "(none)" for _, m, _ in rows)
    by_outcome = Counter(o or "(none)" for _, _, o in rows)

    real = [(lid, m, o) for lid, m, o in rows if is_outcome(o)]
    assessed = [(lid, m, o) for lid, m, o in rows if not is_outcome(o)]

    return {
        "businesses": total,
        "with_history": len({lid for lid, _, _ in rows}),
        "entries": len(rows),
        "by_method": dict(by_method.most_common()),
        "by_outcome": dict(by_outcome.most_common()),
        # Reported separately and always. "Steps recorded" counted together
        # would let assessments stand in for outcomes, which is exactly how a
        # system that has contacted nobody can look busy.
        "real_outcomes": len(real),
        "businesses_with_an_outcome": len({lid for lid, _, _ in real}),
        "assessments": len(assessed),
        "outcome_breakdown": dict(Counter(o for _, _, o in real).most_common()),
    }
