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
read by whatsapp_sender.CONSENT_OK and smart_outreach. Any value written there becomes a sending
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
    # The business asked us to send details on WhatsApp. This is the ONLY
    # thing in the system that creates WhatsApp permission, and it creates it
    # the way Meta requires: the business asked, on a recorded call, in its own
    # words. It lands on AI_INTEREST_DETECTED because someone who wants the
    # catalogue is interested — the opt-in is a side effect, not the stage.
    #
    # Note what is NOT here: whatsapp_verified. Verification proves an account
    # exists on a number. It is a technical fact and never a permission, and
    # wiring "verified -> send" would be this codebase's eighth instance of
    # turning a fact into a licence to contact someone.
    "WHATSAPP_OPT_IN": AI_INTEREST_DETECTED,
    "INTERESTED": AI_INTEREST_DETECTED,
    # Richer interest signals than a bare "yes, interested" — kept as distinct
    # keys (not folded into INTERESTED) so founder_brief() and reporting can
    # show which ones actually asked for a next step, without opening a new
    # TRANSITIONS edge. Promoting AI_INTEREST_DETECTED -> FOUNDER_CALL_REQUESTED
    # stays request_founder_call()'s job, same as it already is for INTERESTED;
    # the AI's tool call does not get to skip that step just because the ask
    # was more specific.
    "MEETING_REQUESTED": AI_INTEREST_DETECTED,
    "CALLBACK_REQUESTED": AI_INTEREST_DETECTED,
    "HUMAN_HANDOFF": AI_INTEREST_DETECTED,
    # Distinct from WHATSAPP_OPT_IN on purpose: "send me details" without the
    # word WhatsApp must never manufacture WhatsApp consent — that would be
    # this codebase's ninth instance of turning a fact (they want info) into
    # a licence (we may message this channel). Interested enough to want
    # something sent, consent unresolved either way.
    "SEND_INFO_EMAIL": AI_INTEREST_DETECTED,
    "NOT_INTERESTED": AI_NOT_INTERESTED,
    # Right business, wrong individual ("I'm not the decision maker"). Same
    # disposition as NOT_INTERESTED for this contact path — nothing here
    # licenses trying a different number at the same business, since that
    # would need its own phone record and its own eligibility check, not an
    # inference from this call.
    "WRONG_PERSON": AI_NOT_INTERESTED,
    "NO_ANSWER": AI_NO_ANSWER,
    # Engine-detected (voicemail greeting pattern match), not something the
    # model decides — see livekit-agent.js VOICEMAIL_PATTERNS. Same terminal
    # stage as NO_ANSWER: this system is one attempt per lead regardless of
    # who or what picked up (test_no_answer_does_not_license_a_retry).
    "VOICEMAIL": AI_NO_ANSWER,
    # Engine-reported on a technical failure (dropped call, provider error)
    # before any outcome could be established. Same terminal stage as
    # NO_ANSWER for the same one-shot reason — a failed dial is not evidence
    # of anything about the lead, but it still consumed the one attempt, and
    # "the connection dropped" retried indefinitely is its own kind of
    # unwanted-contact bug.
    "FAILED": AI_NO_ANSWER,
    "WRONG_NUMBER": AI_WRONG_NUMBER,
    "OPT_OUT": AI_OPTED_OUT,
    # Catch-all for a real, connected conversation that fits none of the
    # above. Deliberately conservative: routes to NOT_INTERESTED rather than
    # INTERESTED, because an unclassifiable result is not evidence of
    # interest and this system does not get a second attempt to find out.
    "OTHER": AI_NOT_INTERESTED,
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

# What the AI may NOT say, sent with every dispatch.
#
# Tested against the live model before writing this: asked "what is your
# price?" -- the single most predictable question on a cold coffee call --
# sarvam-105b-conversations answered "Our wholesale price for Purity Beans
# instant coffee is Rs 450 per kilogram." Nobody gave it a price. It invented
# one, fluently, in the voice of the business.
#
# The profile's _scope_note already says "the AI never negotiates price or
# takes an order", but a note in a JSON file is not an instruction to a model.
# This is.
#
# The allowed list mirrors COMPANY_PROFILE in gov_revenue_engine.py. The
# forbidden list is the same one the email copy has honoured for months: no
# turnover, no ISO, no capacity, no past government supply, no client names.
CALL_CONSTRAINTS = (
    "Never state a price, a discount, or a delivery date. If asked, say the "
    "founder will confirm exact pricing and offer to have him call.",
    "Never take an order or commit to a quantity.",
    "Only these claims are permitted: 100% coffee, zero chicory, no fillers, "
    "no artificial flavours, FSSAI licensed, GST and MSME registered, "
    "PAN-India dispatch, food-grade glass jars.",
    "Never claim turnover, ISO certification, manufacturing capacity, past "
    "government supply, or name any client.",
    "If you do not know something, say you will have the founder confirm it. "
    "Do not guess.",
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
    whatsapp_sender would accept as permission, this raises rather than
    letting the escalation ship.
    """
    from app.services.whatsapp_sender import CONSENT_OK

    leaked = set(STAGES) & set(CONSENT_OK)
    if leaked:
        raise AssertionError(
            "pipeline stage(s) %s are also treated as consent. A call-pipeline "
            "state must never grant a sending permission." % sorted(leaked))


# ----------------------------------------------------------------- gates ----

def stage_of(lead) -> str:
    return (getattr(lead, "outreach_stage", None) or ELIGIBLE).upper()


def is_shared_across_many_leads(value, db, record_id=None) -> bool:
    """True if this exact phone/WhatsApp number is already on record for
    identity.SHARED_PHONE_THRESHOLD or more OTHER leads. A real subscriber's
    number does not simultaneously belong to dozens of unrelated businesses;
    that pattern is placeholder data, a merge/enrichment bug, or a stale
    seed — not a dialable, message-able destination for any one of them.

    Lives here rather than in identity.py: it needs B2BLead, and identity
    must stay a leaf module (test_identity_imports_nothing_from_the_app) so
    every service that already depends on it — including this one — can do
    so without a cycle. outreach_orchestrator already imports from this
    module, so its two WhatsApp gates reuse this instead of a third copy.

    Compares normalised digits in Python rather than a SQL LIKE on the raw
    column: stored numbers use inconsistent separators ("+91-73260-59369"
    vs "+91 73260 59369"), and a dash-vs-space mismatch would silently make
    a LIKE-based version a no-op — found by this function's own test failing
    against a realistically-formatted number, not by inspection.
    """
    import re as _re

    from app.services.identity import SHARED_PHONE_THRESHOLD

    if not value or db is None:
        return False
    digits = _re.sub(r"\D", "", str(value))
    if not digits:
        return False
    target_tail = digits[-10:]

    from sqlalchemy import or_
    from app.models.models import B2BLead

    # Both columns: the 2026-09-15 incident this guards against put the same
    # placeholder value in whatsapp_number on some rows and phone on others,
    # so a number that looks clean in one column can still be the same
    # contaminated value seen from the other. A cheap non-null prefilter
    # narrows the scan; the actual comparison is the normalised digit tail.
    rows = db.query(B2BLead.id, B2BLead.phone, B2BLead.whatsapp_number).filter(
        or_(B2BLead.phone.isnot(None), B2BLead.whatsapp_number.isnot(None))
    )
    if record_id is not None:
        rows = rows.filter(B2BLead.id != record_id)

    count = 0
    for _id, phone, wa in rows:
        for candidate in (phone, wa):
            if candidate and _re.sub(r"\D", "", str(candidate))[-10:] == target_tail:
                count += 1
                break
        if count >= SHARED_PHONE_THRESHOLD:
            return True
    return False


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

    # A phone that exists is not the same question as a phone that is real.
    # These two detectors already existed (contact_enricher's fabrication
    # sweep found 67 id-derived fake numbers live on 2026-07-17) but were
    # only ever wired into a MANUAL, on-demand cleanup endpoint
    # (endpoints.quarantine_fabricated) — never into this gate. A fabricated
    # number introduced after the last manual run would pass every other
    # check here and could be dialed; at best that wastes the one attempt
    # this lead ever gets, at worst an id-derived digit run coincides with a
    # real subscriber who never asked to be called.
    from app.services import identity
    if identity.is_fabricated_pattern(phone):
        return False, f"phone looks fabricated (placeholder pattern): {phone}"
    if identity.is_id_derived_phone(phone, getattr(lead, "id", None)):
        return False, f"phone looks mechanically derived from this lead's own id: {phone}"
    # object_session, not a new db parameter: lead is already loaded through
    # one, and every existing caller of may_place_ai_call(lead) would need to
    # start passing a session otherwise. See is_shared_across_many_leads
    # above for why this check exists — containment for an unroot-caused
    # incident, not a fix for its cause.
    from sqlalchemy.orm import object_session
    _session = object_session(lead)
    if _session is not None and is_shared_across_many_leads(phone, _session, getattr(lead, "id", None)):
        return False, f"phone is on record for {identity.SHARED_PHONE_THRESHOLD}+ other leads — not a dialable number for this one: {phone}"

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

    if key == "WHATSAPP_OPT_IN":
        # Consent is written by whatsapp_consent.record and nowhere else. This
        # branch used to set the four consent fields and the NEXT_ACTION_SET
        # event itself, which made it the second of three writers; the third
        # (the email-reply path) would have been the fourth idea of what to
        # record. The vocabulary of valid sources, the binding to a specific
        # number, the evidence row and the commitment event all live in that
        # module now.
        #
        # The evidence is the business's own words from this call, which is
        # what "who said we could" has to be answerable with years later.
        from app.services import whatsapp_consent

        whatsapp_consent.record(
            lead, db,
            source="AI_CALL_WHATSAPP_REQUEST",
            evidence=(summary or "asked for details on WhatsApp during the AI call"),
        )

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
    """Promote an interested lead into the founder queue and create an actionable founder-call work item. This does not place/dial the call."""
    from app.models.models import WorkflowEvent, WorkflowExecution

    if stage_of(lead) == FOUNDER_CALL_REQUESTED:
        stage = FOUNDER_CALL_REQUESTED
    else:
        stage = advance(lead, db, FOUNDER_CALL_REQUESTED, note=note)
    existing = (db.query(WorkflowExecution)
                  .filter(WorkflowExecution.lead_id == lead.id,
                          WorkflowExecution.workflow_type == "FOUNDER_CALL",
                          WorkflowExecution.status == "REQUESTED")
                  .first())
    if existing is None:
        db.add(WorkflowExecution(
            workflow_type="FOUNDER_CALL", lead_id=lead.id, status="REQUESTED",
            requested_by="AI",
            payload={"reason": "AI requested human handoff",
                     "note": (note or "")[:500],
                     "founder_brief": founder_brief(lead)},
        ))
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="FOUNDER_CALL_REQUESTED",
            actor="AI", channel="call",
            payload={"reason": "AI requested human handoff",
                     "note": (note or "")[:500]},
            occurred_at=datetime.utcnow(),
        ))
    return stage


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
