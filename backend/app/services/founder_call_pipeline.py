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

# The line spoken first, before the model takes over.
#
# Short and conversational on purpose: the previous opening ("Hello, this is an
# AI assistant calling on behalf of Pure Pantry Provisions. We supply coffee to
# cafes and businesses. Is this a good time for one quick question?") delivered
# a pitch before the person had agreed to listen, which is what makes a call
# sound like telemarketing. This one asks for the minute and lets the
# conversation earn the next one.
#
# The AI disclosure stays in the first sentence. Founder decision, 2026-09-18,
# choosing it over "disclose only if asked": it is one clause of the lawful
# basis in this module's docstring, and script_discloses() refuses to dial
# without it.
OPENING_DISCLOSURE = (
    "Hi, this is an AI assistant calling on behalf of Purity Beans. "
    "Do you have a quick minute?"
)

# Words that sit in contact_name but are not a person's name. Greeting a
# business as "Hi Manager" or "Hi Sales" is worse than not using a name at all.
_NOT_A_FIRST_NAME = frozenset({
    "mr", "mrs", "ms", "dr", "sir", "madam", "maam", "shri", "smt",
    "manager", "owner", "admin", "sales", "info", "team", "office",
    "reception", "contact", "purchase", "procurement", "accounts", "hr",
    "support", "director", "proprietor", "partner", "the",
})


def _first_name(lead) -> str:
    """A first name worth saying out loud, or "" when the record does not
    really have one. Scraped contact_name holds roles, companies and
    salutations as often as names, so anything doubtful is dropped."""
    for token in (getattr(lead, "contact_name", "") or "").replace(".", " ").split():
        word = token.strip(",;:()")
        if word.lower() in _NOT_A_FIRST_NAME:
            continue
        if word.isalpha() and 2 <= len(word) <= 20:
            return word[:1].upper() + word[1:].lower()
        return ""
    return ""


def call_context(lead, db) -> dict:
    """What the agent knows before it speaks, taken only from the record.

    Three things, each answering a question a real caller would be expected
    to handle without improvising:

      provenance        "how did you get my number?" -- answered from
                        phone_source / lead_source. Empty when unknown, and the
                        prompt then says so rather than inventing a source.
      business_type     lets the qualifying question be about THEIR business
                        ("you work with packaged grocery") instead of generic.
      previous_contact  "didn't you email us?" -- the last proven email or
                        WhatsApp touch, so the agent never pretends to be a
                        first contact when it is not.

    Nothing here is inferred. A field the record does not hold is "".
    """
    from app.models.models import WorkflowEvent

    business_type = next(
        (v.strip().replace("_", " ") for v in (
            getattr(lead, "division", ""), getattr(lead, "segment", ""),
            getattr(lead, "searched_category", ""),
        ) if (v or "").strip()),
        "",
    )

    previous_contact = ""
    if db is not None and getattr(lead, "id", None):
        last = (db.query(WorkflowEvent)
                .filter(WorkflowEvent.lead_id == lead.id,
                        WorkflowEvent.event_type.in_(("EMAIL_SENT", "WHATSAPP_SENT")))
                .order_by(WorkflowEvent.occurred_at.desc()).first())
        if last is not None and last.occurred_at:
            channel = "an email" if last.event_type == "EMAIL_SENT" else "a WhatsApp message"
            previous_contact = f"we sent {channel} on {last.occurred_at:%d %b %Y}"

    return {"provenance": _provenance(lead), "business_type": business_type[:120],
            "previous_contact": previous_contact}


# Public listings a business can recognise as where its number is published.
# phone_source also names the search tools that FOUND those listings
# (Perplexity, BraveSearch); they are not where the number lives, so they are
# not named on a call. UNVERIFIED_IMPORT maps to nothing: an unknown source is
# said to be unknown.
_PUBLIC_LISTINGS = {
    "googleplaces": "Google Maps", "google maps": "Google Maps",
    "indiamart": "IndiaMART", "tradeindia": "TradeIndia", "justdial": "Justdial",
    "openstreetmap": "OpenStreetMap",
    "nestle_distributor_locator": "a public distributor listing",
}


def _provenance(lead) -> str:
    """Where the business's number is publicly listed, in words it would know."""
    names = []
    raw = f"{getattr(lead, 'phone_source', '') or ''},{getattr(lead, 'lead_source', '') or ''}"
    for part in raw.split(","):
        name = _PUBLIC_LISTINGS.get(part.strip().lower())
        if name and name not in names:
            names.append(name)
    return " and ".join(names[:2])


def opening_for(lead) -> str:
    """The opening for this lead: their first name when we genuinely know it.

    Built from OPENING_DISCLOSURE rather than written separately, so the gate
    that checks OPENING_DISCLOSURE is checking the words this lead will hear.
    may_place_ai_call runs script_discloses() on this exact string.
    """
    name = _first_name(lead)
    return OPENING_DISCLOSURE.replace("Hi,", f"Hi {name},", 1) if name else OPENING_DISCLOSURE

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
    "Never state a price, and never estimate one. The same applies to margins, "
    "discounts, minimum order quantities, credit terms, territory or "
    "exclusivity, delivery timelines and sales figures. If asked, say: "
    "\"I don't want to give you an incorrect figure. I'll have the team share "
    "the current commercial terms with you.\"",
    "Never take an order or commit to a quantity.",
    "Only these claims are permitted: 100% coffee, zero chicory, no fillers, "
    "no artificial flavours, FSSAI licensed, GST and MSME registered, "
    "PAN-India dispatch, food-grade glass jars.",
    "Never claim turnover, ISO or any certification not listed above, "
    "manufacturing capacity, past government supply, or name any client.",
    "If you do not know something, say the team will confirm it. Do not guess.",
)

# Topics a person should own. When the conversation reaches one, the agent
# offers the team rather than continuing -- the same list the prompt names,
# kept here so the backend and the agent cannot drift apart.
HANDOFF_TOPICS = ("margins", "territory or exclusivity", "credit terms",
                  "a custom or bulk order", "a formal quotation")

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

    # The per-lead line, not the template: that is what this lead will hear.
    ok, why = script_discloses(opening_for(lead))
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


def _call_whatsapp_destination(lead, supplied: str) -> tuple[str, str, str]:
    """Which number a WhatsApp opt-in on this call actually covers.

    The agent asks "can I send it to this number, or would you prefer a
    different one?" and reports only a number they READ OUT; an empty value
    means "this number", i.e. the one that was dialled — lead.phone. That is
    deliberately not whatsapp_sender's destination order (whatsapp_number
    first): a WhatsApp number scraped earlier is not the number they just
    agreed to, and binding consent to it would message a destination nobody
    on this call mentioned.

    Returns (destination, how, refusal). A non-empty refusal means no consent:
    a landline cannot receive WhatsApp, and a number that is not a
    well-formed Indian mobile is more likely a mishearing than a destination.
    """
    import re

    from app.services import identity

    if (supplied or "").strip():
        digits = re.sub(r"\D", "", supplied)
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        digits = digits.lstrip("0")
        if not (len(digits) == 10 and digits[0] in "6789"):
            return "", "", (f"the number given on the call ({supplied!r}) is not a valid "
                            f"Indian mobile — likely misheard; confirm it before messaging")
        return digits, "read out on the call", ""

    dialled = (getattr(lead, "phone", "") or "").strip()
    if not dialled:
        return "", "", "no dialled number on record to bind the opt-in to"
    if identity.is_landline(dialled):
        return "", "", (f"{dialled} is a landline — WhatsApp cannot reach it; "
                        f"ask for a mobile number")
    return dialled, "the number that was called", ""


# What the call learned, beyond the outcome, in a closed vocabulary so it can
# be counted: which channel converts, which objection recurs, how many trade
# leads already carry the category. A value outside a field's set is dropped,
# not mapped to the nearest one -- an absent field reads as "not learned",
# which is true, where a guessed one would read as a fact.
CALL_DETAIL_VALUES = {
    "preferred_channel": frozenset({"WHATSAPP", "EMAIL", "CALL", "NONE"}),
    "handles_instant_coffee": frozenset({"YES", "NO", "UNKNOWN"}),
    "decision_maker": frozenset({"YES", "NO", "UNKNOWN"}),
    "objection": frozenset({"NONE", "EXISTING_SUPPLIER", "PRICE", "NO_NEED",
                            "TIMING", "OTHER"}),
}


def clean_call_details(details) -> dict:
    """Keep only recognised fields holding recognised values."""
    out = {}
    for field, allowed in CALL_DETAIL_VALUES.items():
        value = str((details or {}).get(field) or "").strip().upper()
        if value in allowed:
            out[field] = value
    return out


def record_ai_outcome(lead, db, outcome: str, *, summary: str = "",
                      interest: str = "", callback_window: str = "",
                      transcript: str = "", whatsapp_number: str = "",
                      details: dict | None = None) -> str:
    """Apply one AI call result. The only entry point after a dial.

    Consent is not written here in any branch. An opt-out DOES write
    do_not_call -- that is the business telling us to stop, which is a fact
    about our obligations, not a grant of permission.

    details carries what the call learned beyond the outcome (see
    CALL_DETAIL_VALUES). It is reporting only: nothing in it grants or
    withdraws anything. preferred_channel=WHATSAPP in particular is not
    consent -- only the WHATSAPP_OPT_IN outcome is.
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

        destination, how, refusal = _call_whatsapp_destination(lead, whatsapp_number)
        if refusal:
            # They asked for WhatsApp, but not on a number WhatsApp can reach.
            # Recorded as a fact for a person to follow up, never as consent.
            from app.models.models import WorkflowEvent
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="WHATSAPP_CONSENT_UNBOUND",
                actor="SYSTEM", channel="phone",
                payload={"source": "AI_CALL_WHATSAPP_REQUEST",
                         "evidence": (summary or "")[:1000],
                         "offered_number": whatsapp_number or getattr(lead, "phone", ""),
                         "note": refusal},
                occurred_at=datetime.utcnow()))
        else:
            # The destination is the number they agreed to on this call:
            # either one they read out, or the one they were speaking on. A
            # WhatsApp number scraped earlier is not what they said yes to, so
            # it is replaced rather than consulted.
            lead.whatsapp_number = destination
            whatsapp_consent.record(
                lead, db,
                source="AI_CALL_WHATSAPP_REQUEST",
                evidence=((summary or "asked for details on WhatsApp during the AI call")
                          + f" | WhatsApp destination: {destination} ({how})"),
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

    learned = clean_call_details(details)
    if learned:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="AI_CALL_DETAILS",
            actor="ai_voice_agent", channel="phone",
            payload={"outcome": key, **learned},
            occurred_at=datetime.utcnow()))

    # And on the business's own record, in the same table the founder's own
    # calls write to, so one query gives the whole contact history rather than
    # AI calls in one place and human calls in another.
    from app.services import lead_journal as journal
    journal.record(
        lead, db, method=journal.PHONE, outcome=key,
        remark=(summary or f"AI qualification call concluded {key}")
               + (f" | interest: {interest}" if interest else "")
               + (f" | callback: {callback_window}" if callback_window else "")
               + "".join(f" | {k.replace('_', ' ')}: {v.lower()}"
                         for k, v in learned.items()),
        by="ai_voice_agent", force=True)

    # The business asked for a person. MAX_AI_COLD_CALLS_PER_LEAD is 1, so a
    # callback, a meeting or a handoff can only ever be kept by a human --
    # left at AI_INTEREST_DETECTED, the ask was recorded and nobody was told.
    if key in FOUNDER_CALL_ASKS:
        request_founder_call(
            lead, db, reason=FOUNDER_CALL_ASKS[key], requested_by="AI",
            note=(summary or "")
                 + (f" | callback: {callback_window}" if callback_window else ""))
    # Where the lead actually is -- past `target` when the ask was queued.
    # The webhook reports this, and a retry reports stage_of(); they must agree.
    return stage_of(lead)


# AI outcomes where the business asked to talk to a person. Each creates a
# founder-call work item; plain INTERESTED does not -- that one the founder
# promotes by choice.
FOUNDER_CALL_ASKS = {
    "HUMAN_HANDOFF": "asked on the AI call to speak with the team",
    "CALLBACK_REQUESTED": "asked on the AI call to be called back",
    "MEETING_REQUESTED": "asked on the AI call for a meeting",
}


def _json_safe(value):
    """WorkflowExecution.payload is a JSON column; founder_brief holds datetimes."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def request_founder_call(lead, db, *, note: str = "", reason: str = "",
                         requested_by: str = "FOUNDER") -> str:
    """Put the lead in the founder's call queue: stage FOUNDER_CALL_REQUESTED
    plus one REQUESTED "FOUNDER_CALL" work item carrying the brief. Dials
    nothing. Idempotent: a second request while one is open adds nothing."""
    from app.models.models import WorkflowEvent, WorkflowExecution

    if stage_of(lead) != FOUNDER_CALL_REQUESTED:
        advance(lead, db, FOUNDER_CALL_REQUESTED, note=note)
    if _open_founder_call(lead, db) is None:
        why = reason or "promoted to the founder call queue"
        db.add(WorkflowExecution(
            workflow_type="FOUNDER_CALL", lead_id=lead.id, status="REQUESTED",
            requested_by=requested_by,
            payload={"reason": why, "note": (note or "")[:500],
                     "founder_brief": _json_safe(founder_brief(lead))},
        ))
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="FOUNDER_CALL_REQUESTED",
            actor=requested_by, channel="call",
            payload={"reason": why, "note": (note or "")[:500]},
            occurred_at=datetime.utcnow(),
        ))
    return FOUNDER_CALL_REQUESTED


def _open_founder_call(lead, db):
    from app.models.models import WorkflowExecution
    return (db.query(WorkflowExecution)
              .filter(WorkflowExecution.lead_id == lead.id,
                      WorkflowExecution.workflow_type == "FOUNDER_CALL",
                      WorkflowExecution.status == "REQUESTED")
              .first())


def founder_call_queue(db, limit: int = 50) -> list:
    """Open founder-call work items, oldest first -- the order they were asked for."""
    from app.models.models import WorkflowExecution
    return (db.query(WorkflowExecution)
              .filter(WorkflowExecution.workflow_type == "FOUNDER_CALL",
                      WorkflowExecution.status == "REQUESTED")
              .order_by(WorkflowExecution.requested_at.asc())
              .limit(limit).all())


def complete_founder_call(lead, db, *, outcome: str, note: str = "") -> str:
    """The founder made the call. Closes the open work item, advances the
    stage to FOUNDER_CALL_COMPLETED and writes the founder's own record of it.

    WorkflowEngine's FOUNDER_CALL handler opens a NEW execution each time, so
    it cannot close the item request_founder_call() opened; this can.
    """
    from app.models.models import WorkflowEvent

    outcome = (outcome or "").strip()
    if not outcome:
        raise ValueError("a completed founder call needs an outcome")
    if stage_of(lead) != FOUNDER_CALL_REQUESTED:
        raise ValueError(f"lead {lead.id} is at {stage_of(lead)}, not in the founder call queue")

    item = _open_founder_call(lead, db)
    advance(lead, db, FOUNDER_CALL_COMPLETED, note=f"{outcome}: {note}"[:300])
    if item is not None:
        item.status = "COMPLETED"
        item.finished_at = datetime.utcnow()
        item.result = {"outcome": outcome, "note": (note or "")[:500]}
    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="FOUNDER_CALL_COMPLETED",
        actor="FOUNDER", channel="call",
        payload={"outcome": outcome, "note": (note or "")[:500]},
        occurred_at=datetime.utcnow()))

    from app.services import lead_journal as journal
    journal.record(lead, db, method=journal.PHONE, outcome=outcome[:40],
                   remark=note or "founder call completed", by="founder", force=True)
    return FOUNDER_CALL_COMPLETED


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
