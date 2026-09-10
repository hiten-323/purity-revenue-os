r"""
AI qualification call -> founder call. The state machine and its invariants.

The operating model
-------------------
    LEAD
      -> eligibility + preference-registry scrub
      -> ONE AI introductory call, disclosed as AI
      -> interested?  -> founder call queue -> founder call -> opportunity
         not interested / opted out / wrong number -> stop
         no answer -> stop automating; manual follow-up only

The AI qualifies interest. It does not close, and it does not manufacture
consent. Those are three different things and this module keeps them apart.

Why a separate column instead of consent_status
-----------------------------------------------
The obvious implementation is to write the pipeline states into
`consent_status`. That would be a bug, and a familiar one. `consent_status` is
read by whatsapp_sender.CONSENT_OK, smart_outreach (:507) and
CallingAgentService.CALL_ALLOWED_IF. Any value written there becomes a sending
permission somewhere else the moment it exists, so "an AI rang this shop once"
would silently become "this number may be messaged on WhatsApp" under Meta's
opt-in rules -- without the shop doing anything.

So the pipeline lives in its own column, `outreach_stage`, which nothing else
reads. STAGES and the consent vocabularies are asserted disjoint by
assert_consent_neutral(), and a test pins it so a future edit cannot quietly
merge them.

    AI_INTEREST_DETECTED  !=  EXPLICIT consent
    AI_CALL_ATTEMPTED     !=  WhatsApp permission
    AI_CALL_ATTEMPTED     !=  email permission

The one lawful basis this module DOES assert
--------------------------------------------
This gate deliberately does not require recorded consent, because a first
introductory call is what would produce it. That is a real decision with a real
basis, and the basis is narrow: a business number the business itself published,
called about a business matter, disclosed as AI, once, scrubbed against a
preference registry, and honoured immediately on request to stop.

Every one of those clauses is enforced below. Remove any of them and the basis
is gone -- which is why they live in code rather than in a policy document.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ---------------------------------------------------------------- states ----

ELIGIBLE = "ELIGIBLE"
AI_CALL_ATTEMPTED = "AI_CALL_ATTEMPTED"
AI_NO_ANSWER = "AI_NO_ANSWER"
AI_NOT_INTERESTED = "AI_NOT_INTERESTED"
AI_WRONG_NUMBER = "AI_WRONG_NUMBER"
AI_OPTED_OUT = "AI_OPTED_OUT"
AI_INTEREST_DETECTED = "AI_INTEREST_DETECTED"
FOUNDER_CALL_REQUESTED = "FOUNDER_CALL_REQUESTED"
FOUNDER_CALL_COMPLETED = "FOUNDER_CALL_COMPLETED"
COMMERCIAL_OPPORTUNITY = "COMMERCIAL_OPPORTUNITY"
CLOSED_NO_FIT = "CLOSED_NO_FIT"

STAGES = (
    ELIGIBLE, AI_CALL_ATTEMPTED, AI_NO_ANSWER, AI_NOT_INTERESTED,
    AI_WRONG_NUMBER, AI_OPTED_OUT, AI_INTEREST_DETECTED,
    FOUNDER_CALL_REQUESTED, FOUNDER_CALL_COMPLETED, COMMERCIAL_OPPORTUNITY,
    CLOSED_NO_FIT,
)

# A stage nothing may leave. Automation stops here; a human may still act.
TERMINAL = frozenset({
    AI_NO_ANSWER, AI_NOT_INTERESTED, AI_WRONG_NUMBER, AI_OPTED_OUT,
    COMMERCIAL_OPPORTUNITY, CLOSED_NO_FIT,
})

TRANSITIONS: dict[str, frozenset[str]] = {
    ELIGIBLE: frozenset({AI_CALL_ATTEMPTED}),
    AI_CALL_ATTEMPTED: frozenset({
        AI_INTEREST_DETECTED, AI_NOT_INTERESTED, AI_NO_ANSWER,
        AI_WRONG_NUMBER, AI_OPTED_OUT,
    }),
    AI_INTEREST_DETECTED: frozenset({FOUNDER_CALL_REQUESTED, AI_OPTED_OUT}),
    FOUNDER_CALL_REQUESTED: frozenset({FOUNDER_CALL_COMPLETED, AI_OPTED_OUT}),
    FOUNDER_CALL_COMPLETED: frozenset({COMMERCIAL_OPPORTUNITY, CLOSED_NO_FIT}),
}

# The AI's outcome vocabulary, mapped to exactly one stage each. A provider
# that reports something not in here is an unknown outcome, never a guessed one.
OUTCOMES: dict[str, str] = {
    "INTERESTED": AI_INTEREST_DETECTED,
    "NOT_INTERESTED": AI_NOT_INTERESTED,
    "NO_ANSWER": AI_NO_ANSWER,
    "WRONG_NUMBER": AI_WRONG_NUMBER,
    "OPT_OUT": AI_OPTED_OUT,
}

# ----------------------------------------------------------- invariants ----

MAX_AI_COLD_CALLS_PER_LEAD = 1
MAX_AI_CALLS_PER_DAY = 25
# Every business category we are willing to call. Founder decision, 2026-09-10:
# all of them.
#
# Two things made the old list wrong, and both are worth naming because the
# shape recurs:
#
# 1. It held five names — corporate, gifting, distributor, retail, horeca —
#    against data that actually uses 26. It had `horeca` but not `cafe`,
#    `retail` but not `grocery`, `corporate` but not `corporate_office`. Those
#    are near-synonyms that never matched, so 618 phone-bearing businesses were
#    refused as "not callable" without anyone deciding that. Cafes are the first
#    entry on the stated priority list.
#
# 2. There were TWO such lists. CallingAgentService.CALLABLE_SEGMENTS gated
#    `lead.division`; this one gated `lead.segment`; the two columns use
#    different vocabularies. A lead could pass one gate and fail the other for
#    the same business. calling_agent now imports this set, so there is one.
#
# Both vocabularies are included deliberately: whichever column a caller reads,
# the answer is the same.
CALLABLE_CATEGORIES = frozenset({
    # segment vocabulary
    "corporate", "grocery", "distributor", "horeca", "corporate_office",
    "cafe", "wholesaler", "facility_management", "hospital",
    # division vocabulary
    "restaurant", "retail_kirana", "supermarket", "hotel", "manufacturing",
    "modern_trade", "school", "college", "office_pantry", "institutional_buyer",
    # carried from the previous list; not in the data today, may return
    "gifting", "retail",
    # a recorded category meaning "we looked and could not tell", which is not
    # the same as a blank field. Blank still fails below: an uncategorised row
    # is an incomplete record, not a business type.
    "unknown",
})

# Kept as the old name so existing call sites and messages keep working.
CALLABLE_SEGMENTS = tuple(sorted(CALLABLE_CATEGORIES))

# ------------------------------------------------------------- disclosure --

OPENING_DISCLOSURE = (
    "Hello, this is an AI assistant calling on behalf of Pure Pantry "
    "Provisions. We supply coffee to cafes and businesses. Is this a good "
    "time for one quick question?"
)

QUALIFICATION_QUESTIONS = (
    "Are you the person who handles coffee or procurement here?",
    "Are you buying coffee commercially at the moment?",
    "Would you be open to hearing about an alternative supplier?",
    "Would you like our founder to call you directly?",
)

# Markers that must survive any rewrite of the opening line.
_DISCLOSURE_MARKERS = (
    ("ai", "automated", "artificial intelligence"),
    ("pure pantry", "purity"),
)


def script_discloses(text: str) -> tuple[bool, str]:
    """An opening line that does not say it is an AI is not this pitch."""
    low = (text or "").lower()
    for group in _DISCLOSURE_MARKERS:
        if not any(marker in low for marker in group):
            return False, "opening line must identify one of %r" % (group,)
    return True, "discloses AI and identifies the business"


def assert_consent_neutral() -> None:
    """No pipeline stage may be a consent value anywhere else.

    Imported and called by the test suite. If a later edit adds a stage that
    whatsapp_sender or the calling agent would accept as permission, this
    raises rather than letting the escalation ship.
    """
    from app.services.calling_agent import CallingAgentService
    from app.services.whatsapp_sender import CONSENT_OK

    leaked = set(STAGES) & (
        set(CONSENT_OK) | set(CallingAgentService.CALL_ALLOWED_IF))
    if leaked:
        raise AssertionError(
            "pipeline stage(s) %s are also treated as consent. A call-pipeline "
            "state must never grant a sending permission." % sorted(leaked))


# ----------------------------------------------------------------- gates ----

def stage_of(lead) -> str:
    return (getattr(lead, "outreach_stage", None) or ELIGIBLE).upper()


def may_place_ai_call(lead) -> tuple[bool, str]:
    """The single authority on whether the ONE cold qualification call is
    permitted. Reasons are returned, never raised, so a batch reports instead
    of aborting.

    Note what is deliberately absent: a consent_status check. This call is the
    thing that would produce consent, so requiring it first is circular. Every
    other clause of the basis is checked instead.
    """
    from app.services import preference_registry

    if getattr(lead, "do_not_call", False):
        return False, "do_not_call is set on this lead"

    stage = stage_of(lead)
    if stage != ELIGIBLE:
        return False, ("already in the pipeline at %s; the AI cold call is "
                       "once per lead" % stage)

    placed = getattr(lead, "ai_call_count", 0) or 0
    if placed >= MAX_AI_COLD_CALLS_PER_LEAD:
        return False, ("ai_call_count is already %d; limit is %d"
                       % (placed, MAX_AI_COLD_CALLS_PER_LEAD))

    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return False, "no phone on record"

    allowed, why = preference_registry.check(phone)
    if not allowed:
        return False, "preference registry: %s" % why

    segment = (getattr(lead, "segment", "") or "").strip().lower()
    if segment not in CALLABLE_SEGMENTS:
        return False, ("segment %s is not callable; allowed: %s"
                       % (segment or "(none)", ", ".join(CALLABLE_SEGMENTS)))

    ok, why = script_discloses(OPENING_DISCLOSURE)
    if not ok:
        return False, "opening script fails disclosure: %s" % why

    from app.observability import ALLOWED, decision
    decision("phone.cold_call_gate", ALLOWED,
             "eligible for one disclosed AI qualification call", lead=lead,
             company=getattr(lead, "company", ""), segment=segment)
    return True, "eligible for one disclosed AI qualification call"


def calls_placed_today(db) -> int:
    from app.models.models import CallHistory
    since = datetime.utcnow() - timedelta(hours=24)
    return (db.query(CallHistory)
              .filter(CallHistory.call_date >= since,
                      CallHistory.status.like("AI_%"))
              .count())


def daily_budget_remaining(db) -> int:
    return max(0, MAX_AI_CALLS_PER_DAY - calls_placed_today(db))


# ------------------------------------------------------------ transitions --

def advance(lead, db, to_stage: str, *, note: str = "") -> str:
    """Move one step, or refuse. Arbitrary jumps are not possible."""
    to_stage = (to_stage or "").upper()
    if to_stage not in STAGES:
        raise ValueError("%r is not a pipeline stage" % to_stage)

    frm = stage_of(lead)
    if frm in TERMINAL:
        raise ValueError("%s is terminal; nothing follows it" % frm)
    legal = TRANSITIONS.get(frm, frozenset())
    if to_stage not in legal:
        raise ValueError(
            "%s -> %s is not a legal transition. From %s the only moves are: %s"
            % (frm, to_stage, frm, ", ".join(sorted(legal)) or "(none)"))

    lead.outreach_stage = to_stage
    lead.outreach_stage_at = datetime.utcnow()
    _event(lead, db, frm, to_stage, note)

    from app.observability import decision
    decision("pipeline.advance", to_stage, note or f"{frm} -> {to_stage}",
             lead=lead, company=getattr(lead, "company", ""), was=frm)
    return to_stage


def _event(lead, db, frm: str, to: str, note: str) -> None:
    try:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="FOUNDER_CALL_PIPELINE",
            before_status=frm, after_status=to,
            payload={"note": note[:500]} if note else None,
            occurred_at=datetime.utcnow()))
    except Exception:
        # An audit row that cannot be written must not cancel the state change
        # the caller already committed to; the caller's own commit still
        # records the stage itself.
        pass


def record_ai_outcome(lead, db, outcome: str, *, summary: str = "",
                      interest: str = "", callback_window: str = "",
                      transcript: str = "") -> str:
    """Apply one AI call result. The only entry point after a dial.

    Consent is not written here in any branch. An opt-out DOES write
    do_not_call -- that is the business telling us to stop, which is a fact
    about our obligations, not a grant of permission.
    """
    key = (outcome or "").strip().upper()
    if key not in OUTCOMES:
        raise ValueError(
            "unknown call outcome %r; expected one of %s. An unrecognised "
            "provider result is not evidence of anything and is never guessed."
            % (outcome, ", ".join(sorted(OUTCOMES))))

    if stage_of(lead) == ELIGIBLE:
        advance(lead, db, AI_CALL_ATTEMPTED, note="AI qualification call placed")
        lead.ai_call_count = (getattr(lead, "ai_call_count", 0) or 0) + 1

    target = OUTCOMES[key]
    advance(lead, db, target, note=summary[:300])

    if summary:
        lead.call_summary = summary[:2000]
    if transcript:
        lead.call_transcript = transcript[:20000]
    if interest:
        lead.ai_interest_level = interest.strip().upper()[:20]
    if callback_window:
        lead.founder_callback_window = callback_window.strip()[:120]
    lead.call_outcome_last = key
    lead.last_call_date = datetime.utcnow()

    if target == AI_OPTED_OUT:
        lead.do_not_call = True
        lead.dnc_reason = ("asked to stop on AI call %s"
                           % datetime.utcnow().strftime("%Y-%m-%d"))
        try:
            from app.services import preference_registry
            if preference_registry.registry_path():
                preference_registry.add([lead.phone])
        except Exception:
            pass

    from app.models.models import CallHistory
    db.add(CallHistory(lead_id=lead.id, call_date=datetime.utcnow(),
                       status="AI_%s" % key, summary=summary[:1000] or None,
                       call_status="COMPLETED"))

    # And on the business's own record, in the same table the founder's own
    # calls write to, so one query gives the whole contact history rather than
    # AI calls in one place and human calls in another.
    from app.services import lead_journal as journal
    journal.record(
        lead, db, method=journal.PHONE, outcome=key,
        remark=(summary or f"AI qualification call concluded {key}")
               + (f" | interest: {interest}" if interest else "")
               + (f" | callback: {callback_window}" if callback_window else ""),
        by="ai_voice_agent", force=True)
    return target


def request_founder_call(lead, db, *, note: str = "") -> str:
    """Promote an interested lead into the founder queue."""
    return advance(lead, db, FOUNDER_CALL_REQUESTED, note=note)


def founder_queue(db, limit: int = 50):
    from app.models.models import B2BLead
    return (db.query(B2BLead)
              .filter(B2BLead.outreach_stage.in_(
                  [AI_INTEREST_DETECTED, FOUNDER_CALL_REQUESTED]))
              .order_by(B2BLead.outreach_stage_at.asc())
              .limit(limit).all())


def founder_brief(lead) -> dict:
    """Everything the founder needs before dialling, and nothing invented.

    A field with no recorded value comes back as None rather than a plausible
    default -- an unknown current supplier must not read as "none".
    """
    def val(attr):
        v = getattr(lead, attr, None)
        if v is None or v == "":
            return None
        return v

    return {
        "business": val("company"),
        "segment": val("segment"),
        "city": val("city"),
        "contact": val("contact_name"),
        "phone": val("phone"),
        "phone_provenance": val("phone_source"),
        "currently_uses": val("current_brand") or val("current_supplier"),
        "interest_level": val("ai_interest_level"),
        "ai_call_summary": val("call_summary"),
        "objection": val("objection_reason"),
        "requested_callback": val("founder_callback_window"),
        "stage": stage_of(lead),
        "stage_since": val("outreach_stage_at"),
        "ai_called_at": val("last_call_date"),
    }
