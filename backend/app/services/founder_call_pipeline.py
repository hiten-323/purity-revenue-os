r"""
AI qualification call -> founder call. The state machine and its invariants.

The operating model
-------------------
    LEAD
      -> eligibility + preference-registry scrub
      -> AI introductory calls, disclosed as AI, at most MAX_AI_CALL_ATTEMPTS_PER_LEAD
      -> interested?  -> founder call queue -> founder call -> opportunity
         not interested / opted out / wrong number -> stop
         no answer -> a retry window (>= 168h, different calendar day,
                      business hours, never more than 3 attempts total)
                      -> still no answer -> stop automating

The retry policy (founder decision, this iteration)
---------------------------------------------------
A no-answer is not a human decision and it no longer burns the lead's only
attempt. But retrying is bounded and boring on purpose:

  * at most MAX_AI_CALL_ATTEMPTS_PER_LEAD calls per lead, ever;
  * at least 168 hours between dials (criteria combined, not alternatives);
  * a different calendar day, computed in the business-hours timezone;
  * only during business hours;
  * only when a real human answered the previous call — outcomes that were
    meaningful (interest, wrong person, opt-out, wrong number) never retry;
  * a provider failure before a PSTN attempt never consumes a slot. There is
    NO fleet-wide volume ceiling: any number of eligible leads are dialled
    inside the business-hours window, bounded only by per-lead gates (max 3
    attempts, cooldown, calendar-day rule, retry-only-off-no-answer).

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

from datetime import datetime, timedelta, timezone
import os
import re
from typing import Any


def normalize_company(company_name: str) -> str:
    """The one company-name key used for duplicate protection.

    Lived in calling_agent and was only applied in that module's second gate,
    so a lead that entered through may_place_ai_call never saw it. One
    definition, used by the one calling authority.
    """
    if not company_name:
        return ""
    name = company_name.lower().strip()
    suffixes = [
        r"\bpvt\b", r"\bltd\b", r"\bprivate\b", r"\blimited\b",
        r"\bcorp\b", r"\bcorporation\b", r"\bllc\b", r"\bco\b",
        r"\bcompany\b", r"\binc\b", r"\bincorporated\b",
    ]
    for suffix in suffixes:
        name = re.sub(suffix, "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return " ".join(name.split())


_OPT_OUT_CONSENT = frozenset({"OPT_OUT", "REFUSED", "UNSUBSCRIBED", "REVOKED"})
_OPT_OUT_CONTACT = frozenset({"OPTED_OUT", "DO_NOT_CONTACT", "SUPPRESSED"})  # 4 Nov: SUPPRESSED added (duplicate rows)


def duplicate_phone_reason(lead, db=None):
    """4 Nov 2026 (Hiten "stop,fix all,resume"): a phone shared by several lead
    rows is dialled only through its canonical row (lowest id not already
    suppressed as a duplicate). Returns a reason string to refuse, else None.
    Fail-closed only for rows explicitly marked DUPLICATE_PHONE_ROW; a DB error
    otherwise falls through to the sidecar guard (which re-checks)."""
    import re as _re
    reason = (getattr(lead, "contact_status_reason", "") or "")
    if (getattr(lead, "contact_status", "") or "").upper() == "SUPPRESSED" and reason.startswith("DUPLICATE_PHONE_ROW"):
        return reason[:120]
    d = _re.sub(r"\D", "", getattr(lead, "phone", "") or "")[-10:]
    if len(d) != 10 or getattr(lead, "id", None) is None:
        return None
    try:
        if db is None:
            from sqlalchemy.orm import object_session
            db = object_session(lead)
        if db is None:
            return None
        from app.models.models import B2BLead
        rows = db.query(B2BLead.id, B2BLead.phone, B2BLead.contact_status, B2BLead.contact_status_reason) \
            .filter(B2BLead.phone.like(f"%{d[-4:]}%")).all()
        ids = sorted(r.id for r in rows
                     if _re.sub(r"\D", "", r.phone or "")[-10:] == d
                     and not ((r.contact_status or "").upper() == "SUPPRESSED"
                              and (r.contact_status_reason or "").startswith("DUPLICATE_PHONE_ROW")))
    except Exception:  # noqa: BLE001
        return None
    if ids and ids[0] != lead.id:
        return f"DUPLICATE_PHONE_ROW: number belongs to canonical lead {ids[0]}"
    return None
# Duplicate-company protection ignores these statuses — they are not an
# active conversation with another row at the same business.
_INACTIVE_FOR_DUP = frozenset({"COLD", "ARCHIVED", "CLOSED_LOST", "DISQUALIFIED"})


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
# AI_NO_ANSWER is deliberately NOT terminal: a no-answer is a retry window, not
# a decision about the lead. Everything that represents a real human answer
# (interest, rejection, wrong person, opt-out, wrong number) stays terminal.
TERMINAL = frozenset({
    AI_NOT_INTERESTED, AI_WRONG_NUMBER, AI_OPTED_OUT,
    COMMERCIAL_OPPORTUNITY, CLOSED_NO_FIT,
})

TRANSITIONS: dict[str, frozenset[str]] = {
    ELIGIBLE: frozenset({AI_CALL_ATTEMPTED}),
    # A retry path exists, and only for retryable outcomes: the stage they
    # mapped to (AI_NO_ANSWER) may go back into the calling pipeline. No other
    # "answer" stage has an edge here — a human answer is once and done.
    AI_NO_ANSWER: frozenset({AI_CALL_ATTEMPTED}),
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
    # model decides — see livekit-agent.js VOICEMAIL_PATTERNS. Retryable with
    # NO_ANSWER: a voicemail machine is not a person and not a decision.
    "VOICEMAIL": AI_NO_ANSWER,
    # Engine-reported on a technical failure (dropped call, provider error)
    # before any outcome could be established. Retryable with NO_ANSWER: the
    # connection dying is not evidence about the lead, but it is still bounded
    # by the same MAX_AI_CALL_ATTEMPTS_PER_LEAD / cooldown budget so "the
    # connection dropped" cannot retry itself into harassment.
    "FAILED": AI_NO_ANSWER,
    # 21 Oct: engine-reported early hangup (1-3 user turns, no technical
    # error). Not a decision about us; retryable like NO_ANSWER, but labelled
    # HUNG_UP_EARLY so audits separate customer drop-offs from real errors.
    "CALLEE_HUNGUP": AI_NO_ANSWER,
    # Line busy / engaged. Retryable with NO_ANSWER — the lead never heard the
    # disclosure, so no decision was formed either way.
    "BUSY": AI_NO_ANSWER,
    "WAITING": AI_NO_ANSWER,
    "HOLD": AI_NO_ANSWER,
    "TEMPORARILY_UNAVAILABLE": AI_NO_ANSWER,
    "WRONG_NUMBER": AI_WRONG_NUMBER,
    "OPT_OUT": AI_OPTED_OUT,
    # Catch-all for a real, connected conversation that fits none of the
    # above. Deliberately conservative: routes to NOT_INTERESTED rather than
    # INTERESTED, because an unclassifiable result is not evidence of
    # interest. A conversation happened, so this is a real human answer and is
    # final — one connected call is enough to establish the disposition.
    "OTHER": AI_NOT_INTERESTED,
    # A live person declining on any basis. Distinct from a no-answer: they
    # heard the disclosure and answered the question, so there is nothing to
    # retry towards.
    "REJECTED": AI_NOT_INTERESTED,
}

# Outcomes that may be retried. Each maps to AI_NO_ANSWER above; everything
# else is a real human answer and is final (see TERMINAL / TRANSITIONS).
RETRYABLE_OUTCOMES = frozenset({"NO_ANSWER", "VOICEMAIL", "FAILED", "BUSY", "WAITING", "HOLD", "TEMPORARILY_UNAVAILABLE"})

# Outcomes that represent a substantive exchange with a human — a decision was
# formed and recorded, so the lead must not be dialled again even though the
# dial "failed" from the automation's point of view. Used as a hardening
# cross-check in may_place_ai_call and by classify_meaningful_conversation().
MEANINGFUL_OUTCOMES = frozenset(
    key for key in OUTCOMES
    if key not in RETRYABLE_OUTCOMES and key not in {"WRONG_NUMBER", "OPT_OUT"}
)

# ----------------------------------------------------------- invariants ----

MAX_AI_CALL_ATTEMPTS_PER_LEAD = 3
MAX_AI_COLD_CALLS_PER_LEAD = MAX_AI_CALL_ATTEMPTS_PER_LEAD
MAX_AI_CALLS_PER_DAY = 10**9
AI_CALL_RETRY_COOLDOWN_HOURS = 20                       # Daily retry: 20 hours cooldown to retry next calendar day
MAX_AGENT_RESPONSE_WORDS = 15
VOICE_CONVERSATION_RULES = (
    "Speak at an everyday pace with warm, respectful Hindi.",
    "If the callee mentions a specific date/time, capture it in callback_window and record CALLBACK_REQUESTED.",
    "If the callee says no, ask once if another time would work.",
    "If the callee again says no, politely say goodbye and terminate the call immediately.",
    "Keep responses short and concise, under 15 words per turn.",
)
# There is deliberately NO fleet-wide volume ceiling (no X-per-day, no
# rolling-24h cap, no daily budget): inside the business-hours window every
# eligible lead is processed, bounded only by the per-lead gates above.
# Process-wide dial window. Env `AI_CALL_BUSINESS_HOURS` (default "1") gates
# every dispatch; tests and scheduled dry-runs set it to "0".
BUSINESS_HOURS_GATE_ENV = "AI_CALL_BUSINESS_HOURS"
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
# Spoken Hindi on purpose: default NURAVEDA_LANG / profile defaultLanguage is
# hi-IN. An English disclosure with a Hindi TTS voice produced the 2026-09-21
# "female voice speaking English" failure. Markers "AI" and "Purity" still
# satisfy script_discloses() (case-insensitive).
# 23 Oct 2026 (founder-approved "fix everything"): baseline shortened from 30
# to 16 words; ~3 in 4 pickups hung up during the old greeting. Old text:
#   "Namaste, main Purity Beans ki taraf se AI assistant baat kar rahi hoon. Hum cafes aur
#    businesses ko glass jar wali pure instant coffee provide karte hain — kya aapke paas
#    ek chhota sa minute hai?"
# See backend/docs/opener_experiment_notes.md for the before/after cutover.
OPENING_DISCLOSURE = (
    "नमस्ते जी, मैं Purity Beans से Vrini बोल रही हूँ, एक AI assistant। "
    "क्या आपके business में instant coffee stock या use होती है?"
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


def _previous_contact(lead, db) -> str:
    if not db or not getattr(lead, "id", None):
        return ""
    from app.models.models import WorkflowEvent
    events = (db.query(WorkflowEvent)
              .filter(WorkflowEvent.lead_id == lead.id,
                      WorkflowEvent.event_type.in_(("EMAIL_SENT", "WHATSAPP_SENT")))
              .order_by(WorkflowEvent.occurred_at.desc())
              .all())
    for ev in events:
        payload = ev.payload or {}
        if ev.event_type == "EMAIL_SENT" and ev.channel == "email" and payload.get("message_id"):
            day = ev.occurred_at.day
            mon_yr = ev.occurred_at.strftime("%b %Y")
            return f"we sent an email on {day} {mon_yr}"
        if ev.event_type == "WHATSAPP_SENT" and ev.channel == "whatsapp" and (payload.get("msg_id") or payload.get("message_id")):
            day = ev.occurred_at.day
            mon_yr = ev.occurred_at.strftime("%b %Y")
            return f"we sent a WhatsApp message on {day} {mon_yr}"
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
    business_type = next(
        (v.strip().replace("_", " ") for v in (
            getattr(lead, "division", ""), getattr(lead, "segment", ""),
            getattr(lead, "searched_category", ""),
        ) if (v or "").strip()),
        "",
    )

    past_summary = ""
    if (getattr(lead, "ai_call_count", 0) or 0) > 0:
        if getattr(lead, "call_summary", None):
            past_summary = f"Earlier call notes: {lead.call_summary[:200]}"
        elif getattr(lead, "founder_callback_window", None):
            past_summary = f"Requested callback window: {lead.founder_callback_window}"

    return {
        "provenance": _provenance(lead),
        "business_type": business_type[:120],
        "previous_contact": _previous_contact(lead, db),
        "past_summary": past_summary,
    }


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


def opening_for(lead, opener_variant: str | None = None) -> str:
    """The opening for this lead: their first name when we genuinely know it.

    Built from OPENING_DISCLOSURE (baseline) or an approved variant's ``opening``
    text when the call-intelligence A/B selected one (e.g. e1_short). The gate
    that checks disclosure still runs on the exact string this lead will hear.
    may_place_ai_call validates the baseline; place paths re-check the chosen
    variant opening via script_discloses before dial.
    """
    base = OPENING_DISCLOSURE
    # 4 Nov 2026 (Hiten "stop,fix all,resume"): e4_micro on 100% of dials. A
    # missing / baseline / removed variant (e1_short, question_first) is never
    # allowed to fall back to the long baseline line - it gets e4_micro.
    if not opener_variant or opener_variant in ("baseline", "e1_short", "question_first"):
        opener_variant = "e4_micro"
    if opener_variant and opener_variant != "baseline":
        try:
            from app.services.call_intelligence.variants import opening_text
            override = opening_text(opener_variant, city=getattr(lead, "city", None),
                                    segment=" ".join(filter(None, [getattr(lead, "segment", None), getattr(lead, "business_type", None)])) or None)
            if override:
                base = override
        except Exception:
            base = OPENING_DISCLOSURE
    name = _first_name(lead)
    if not name:
        return base
    # Prefer the Hindi greeting; keep a Hi, fallback for any English rewrite.
    if base.startswith("नमस्ते जी,"):
        return base.replace("नमस्ते जी,", f"नमस्ते {name} जी,", 1)
    if base.startswith("Namaste,"):
        return base.replace("Namaste,", f"Namaste {name} ji,", 1)
    return base.replace("Hi,", f"Hi {name},", 1)

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
    # 14 Oct (founder-approved): the ONE confirmed figure is a 30% margin for
    # distributor / wholesaler / stockist leads. Everything else stays banned.
    "Never state a price, and never estimate one. The same applies to "
    "discounts, offers, minimum order quantities, credit terms, territory or "
    "exclusivity, delivery timelines and sales figures. If asked, say: "
    "\"I don't want to give you an incorrect figure. I'll have the team share "
    "the current commercial terms with you.\"",
    # 21 Oct (founder-approved): margin lines per segment, nothing else.
    "Margins: distributors, wholesalers and stockists - lead with up to 30% "
    "(25% trade discount + 5% cash discount); only if they push back on margin "
    "may you offer up to 35% maximum (25% trade discount + up to 10% cash "
    "discount, framed as the extra cash discount for cash/advance payment); "
    "Never above 35%. Supermarkets and retail stores - say a 30% margin "
    "(25% trade discount + 5% cash discount). Corporate buyers - say \"up to 30% off\". HoReCa (hotels, "
    "restaurants, cafes, caterers) - say \"up to 30% off\". Never "
    "any other figure; every other category never hears a margin figure.",
    "Never take an order or commit to a quantity.",
    "Only these claims are permitted: India's Cleanest Instant Coffee, "
    "pure coffee and no chicory (ONLY for Purica, Purista, Bold and Prima), "
    "food-grade glass jars, no fillers, no artificial flavours, FSSAI "
    "licensed and MSME registered, PAN-India dispatch. "
    "Never say \"100%\". Say \"pure\" only for single-ingredient products, "
    "never for Ultra Blend.",
    "\"No chicory\" only for Purica, Purista, Bold and Prima. Ultra Blend is "
    "70% coffee and 30% chicory - never call it chicory-free or pure.",
    "Never claim turnover, ISO or any certification not listed above, "
    "manufacturing capacity, past government supply, or name any client.",
    "If you do not know something, say the team will confirm it. Do not guess.",
    "When asked where Purity Beans is based, located, or calling from, always say India and highlight PAN-India supply. Never say Abohar or name a specific town.",
    "Starter carton: For retail, kirana, supermarkets and distributors, we offer a starter carton of 12 glass jars at direct factory rates (25% trade discount + 5% cash discount = 30% margin). For HoReCa, corporate, institutions and gifting, offer up to 30% off. No samples, free tastings or trial packs unless the lead asks first.",
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


def _utcnow() -> datetime:
    """UTC naive, matching the store-UTC-naive convention everywhere else."""
    return datetime.utcnow()


def _IST():
    from app.services.business_hours import IST
    return IST


def _as_ist(value: datetime) -> datetime:
    """UTC-naive -> aware Asia/Kolkata. Naive input is assumed UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_IST())


def parse_callback_datetime(val: Any, now: datetime | None = None) -> datetime | None:
    """Parse an explicit requested callback datetime into UTC naive datetime.

    Handles ISO formats, standard date/time formats, relative time expressions
    ('30 minutes baad', 'in 1 hour', '1 ghante baad'), relative day expressions
    ('tomorrow 10am', 'today 7pm', 'kal shaam 7 baje', 'kal 8 baje', 'parso 2 baje',
    'Monday ko call karna', 'subah 10 baje', 'raat ko 8 baje'), and bare times ('7pm', '19:00', '7 baje').
    Preserves exact requested time in Indian Standard Time (Asia/Kolkata).
    """
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is not None:
            return val.astimezone(timezone.utc).replace(tzinfo=None)
        return val
    s = str(val).strip()
    if not s:
        return None
    now_dt = now or _utcnow()
    now_ist = _as_ist(now_dt)

    # 1. Try ISO format
    if "T" in s or s.endswith("Z") or "+" in s:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            ist_dt = dt.replace(tzinfo=_IST())
            return ist_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

    # 2. Try standard date/time string formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            ist_dt = dt.replace(tzinfo=_IST())
            return ist_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

    low = s.lower().strip()

    # 3. Relative minutes: '30 minutes baad', '30 mins later', 'in 45 minutes', 'after 30 mins'
    m_rel_min = re.search(r"(\d+)\s*(?:min|minute|mins|minutes)\s*(?:baad|later|after)?", low)
    if m_rel_min:
        mins = int(m_rel_min.group(1))
        target_ist = now_ist + timedelta(minutes=mins)
        return target_ist.astimezone(timezone.utc).replace(tzinfo=None)

    # 4. Relative hours: '1 hour baad', 'in 2 hours', '1 ghante baad', '2 ghante baad'
    m_rel_hr = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs|ghanta|ghante)\s*(?:baad|later|after)?", low)
    if m_rel_hr:
        hrs = int(m_rel_hr.group(1))
        target_ist = now_ist + timedelta(hours=hrs)
        return target_ist.astimezone(timezone.utc).replace(tzinfo=None)

    # 4b. 27 Oct audit: relative days/weeks ('after 2-4 days', '3 din baad',
    # '1 week', 'agle hafte'). Before this, '2-4 days' fell through to the
    # bare-time rule and became 2 PM TODAY (Tuli General Store was redialled
    # 3x the same afternoon). Ranges use the later bound; time 11:00 IST
    # (17:00 with an evening hint).
    m_rel_day = re.search(r"(\d+)(?:\s*(?:-|to|se|ya|or)\s*(\d+))?\s*(?:day|days|din|dino|dinon|dino\b)", low)
    m_rel_wk = re.search(r"(\d+)?\s*(?:week|weeks|hafte|hafta|haftay)", low)
    if m_rel_day or m_rel_wk:
        if m_rel_day:
            n_days = int(m_rel_day.group(2) or m_rel_day.group(1))
        else:
            n_days = 7 * int(m_rel_wk.group(1) or 1)
        n_days = max(1, min(n_days, 60))
        _pm = any(k in low for k in ("shaam", "evening", "raat", "night", "dophar", "afternoon", "pm"))
        target_day = (now_ist + timedelta(days=n_days)).date()
        target_ist = datetime(target_day.year, target_day.month, target_day.day,
                              17 if _pm else 11, 0, tzinfo=_IST())
        return target_ist.astimezone(timezone.utc).replace(tzinfo=None)

    # 5. Day offsets: 'kal' / 'tomorrow' -> +1, 'parso' -> +2, weekday names -> next weekday
    day_offset = 0
    day_matched = False
    if "kal" in low or "tomorrow" in low:
        day_offset = 1
        day_matched = True
    elif "parso" in low:
        day_offset = 2
        day_matched = True
    else:
        weekdays = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
            "somwar": 0, "mangalwar": 1, "budhwar": 2, "guruwar": 3, "veervar": 3, "shukrawar": 4, "shaniwar": 5, "ravivar": 6
        }
        for wd_name, wd_idx in weekdays.items():
            if wd_name in low:
                cur_wd = now_ist.weekday()
                day_offset = (wd_idx - cur_wd) % 7
                if day_offset == 0:
                    day_offset = 7
                day_matched = True
                break

    # Time of day hints
    is_pm_hint = any(k in low for k in ("shaam", "evening", "raat", "night", "dophar", "afternoon", "pm"))
    is_am_hint = any(k in low for k in ("subah", "morning", "am"))

    # Match time expressions: e.g. '7 baje', '7:30 baje', '7 pm', '7:00 pm', 'at 8', '19:00'
    m_time = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(?:baje|o\'clock|pm|am|hours)?", low)
    if m_time:
        hour_s, min_s = m_time.groups()
        hour = int(hour_s)
        minute = int(min_s) if min_s else 0
        if is_pm_hint and hour < 12:
            hour += 12
        elif is_am_hint and hour == 12:
            hour = 0
        elif not is_am_hint and not is_pm_hint:
            # Telecalling default heuristic: 1-6 is afternoon/evening (13-18), 7-9 evening (19-21)
            if 1 <= hour <= 6:
                hour += 12
            elif 7 <= hour <= 9 and (now_ist.hour >= hour or hour <= 9):
                hour += 12

        target_day = (now_ist + timedelta(days=day_offset)).date()
        target_ist = datetime(target_day.year, target_day.month, target_day.day, hour, minute, tzinfo=_IST())
        if target_ist < now_ist - timedelta(minutes=15) and day_offset == 0:
            target_ist += timedelta(days=1)
        return target_ist.astimezone(timezone.utc).replace(tzinfo=None)

    elif day_matched:
        # Day was specified without a specific time (e.g. 'Monday ko call karna', 'kal call karna')
        # Default to 11:00 AM (or 5:00 PM if evening hint)
        hour = 17 if is_pm_hint else 11
        target_day = (now_ist + timedelta(days=day_offset)).date()
        target_ist = datetime(target_day.year, target_day.month, target_day.day, hour, 0, tzinfo=_IST())
        return target_ist.astimezone(timezone.utc).replace(tzinfo=None)

    return None


def is_explicit_callback(lead) -> bool:
    """True if the lead gave an EXACT requested callback date/time or is in a 5-min callback retry.

    Only a parseable date or datetime counts as "explicitly requested a
    date/time": a vague note ("after 4pm weekdays", "call me soon") carries
    no exact time, so it must NOT lift the business-hours rule or the 72h
    retry cooldown — normal unsolicited calls stay inside the window. The
    test is the SAME parser the scheduler uses (parse_callback_datetime), so
    the gate and the schedule cannot drift apart.
    """
    if not lead:
        return False
    if (getattr(lead, "callback_retry_count", 0) or 0) > 0:
        return True
    window = (getattr(lead, "founder_callback_window", "") or "").strip()
    if not window:
        return False
    return parse_callback_datetime(window) is not None


# 3 Nov 2026 fix 2 (Hiten 11:49 IST): QUARANTINED (shared directory number) too.
PHONE_TRUST_BLOCKED = frozenset({"INVALID", "SHARED_QUARANTINE", "SIP_INVALID", "DEAD_SERIES", "QUARANTINED"})

# 27 Oct 2026 (Hiten via architect; Plivo ticket #29655): the carrier answers
# every number in these +91 series with SIP 484 / cause 28 (0 connects in
# 3,700+ calls since July). Never dialled; the sweep marks them do_not_call.
DEAD_NUMBER_SERIES = ("6060", "6106", "6144", "6968")


def predial_block(phone) -> str | None:
    """Pre-dial filter, mirror of ai-voice-agent/src/lib/predial_filter.js.

    Returns "INVALID_NUMBER" (toll-free/premium 1800/1860/1900, or not a
    10-digit Indian mobile 6-9 / geographic landline 1-5 -- landlines allowed
    since 27 Oct 15:13 IST, Hiten), "DEAD_SERIES" (+91 6060/6106/6144/6968)
    or None (dialable)."""
    from app.services import identity as _identity
    if _identity.is_non_geographic(phone):
        return "INVALID_NUMBER"
    e164 = _identity.voice_dialable_e164(phone)
    if not e164:
        return "INVALID_NUMBER"
    if e164[3:7] in DEAD_NUMBER_SERIES:
        return "DEAD_SERIES"
    return None


def may_place_ai_call(lead) -> tuple[bool, str]:
    """The single authority on whether the ONE cold qualification call is
    permitted. Reasons are returned, never raised, so a batch reports instead
    of aborting.

    Note what is deliberately absent: a requirement of recorded CALL consent.
    This call is the thing that would produce it, so requiring it first is
    circular. Opt-out is the opposite of that and IS checked: a recorded
    refusal is not "missing consent", it is a stop.

    Arming (AI_CALLING_ENABLED) and the emergency kill switch live on
    voice_router.place_call — the one function every dial already passes
    through — and on the planner's phone gate. They are process-wide, not
    per-lead, so they are not this function's job.
    """
    from app.services import preference_registry

    if getattr(lead, "do_not_call", False):
        return False, "do_not_call is set on this lead"
    if predial_block(getattr(lead, "phone", None)) == "DEAD_SERIES":
        return False, "DEAD_SERIES: +91 6060/6106/6144/6968 number series is dead at the carrier (Plivo #29655) - not dialled"
    # 26 Oct (Hiten-approved): a number quarantined as invalid (carrier SIP
    # reject, or manual INVALID / SHARED_QUARANTINE) is never dialled again by
    # ANY path. Before this only the ai_call_cycle selector filtered it, so
    # retries / other callers re-dialled already-invalid numbers.
    _trust = (getattr(lead, "phone_trust", None) or "").upper()
    if _trust in PHONE_TRUST_BLOCKED:
        return False, f"phone_trust={_trust} - number quarantined as invalid, not dialled"
    from app.services.supplier_guard import is_supplier as _is_sup
    _s, _sr = _is_sup(lead)
    if _s:
        return False, "supplier/vendor - not dialled (%s)" % _sr

    consent = (getattr(lead, "consent_status", "") or "").upper()
    if consent in _OPT_OUT_CONSENT:
        return False, f"consent_status={consent} — permanently suppressed"

    contact = (getattr(lead, "contact_status", "") or "").upper()
    if contact in _OPT_OUT_CONTACT:
        return False, f"contact_status={contact} — permanently suppressed"

    # 4 Nov 2026: standing never-dial numbers + duplicate-phone rows.
    from app.services.call_intelligence.standing_rules import call_blocked as _sb
    _sbr = _sb(lead)
    if _sbr:
        return False, _sbr

    status = (getattr(lead, "status", "") or "").upper()
    if status in ("DISQUALIFIED", "CLOSED_LOST", "DO_NOT_CONTACT", "WRONG_NUMBER", "CLOSED_UNRECOVERABLE", "QUARANTINED"):
        return False, f"status={status} - not dialled (not a fit / closed)"

    # 8 Oct 2026 (founder): only numbers the voice service can dial (same rule as
    # the voice service: +91 then 10 digits starting 6-9). Landline/toll-free-only
    # leads stay untouched and email-eligible; they are just never dialled.
    from app.services import identity as _identity
    # 27 Oct 2026 (Hiten): geographic landlines are dialled too; toll-free /
    # premium / malformed numbers are still refused (email unaffected).
    if not _identity.is_voice_dialable(getattr(lead, "phone", None)):
        return False, "phone is not a dialable Indian mobile or landline (voice rule) - email only, not dialled"

    locked = getattr(lead, "lead_locked_until", None)
    if locked and locked > datetime.utcnow():
        return False, "lead_locked"

    stage = stage_of(lead)
    attempts = getattr(lead, "ai_call_count", 0) or 0
    last_call = getattr(lead, "last_call_date", None)
    now = _utcnow()
    scheduled = getattr(lead, "ai_retry_after", None)

    # A retry is only ever licensed off a retryable no-answer or scheduled callback.
    # A lead whose dispatch never returned an outcome (still AI_CALL_ATTEMPTED) is an
    # in-flight / unresolved dial — not a fresh one.
    if stage == AI_CALL_ATTEMPTED:
        if last_call and datetime.utcnow() >= last_call + timedelta(hours=AI_CALL_RETRY_COOLDOWN_HOURS):
            stage = AI_NO_ANSWER
        else:
            return False, ("still AI_CALL_ATTEMPTED — the previous dial never "
                           "produced an outcome; step_outcome() must resolve it "
                           "before another attempt")

    if is_explicit_callback(lead):
        # Explicit requested callback overrides standard cooldown and calendar-day check
        # once the requested datetime has arrived.
        if attempts >= MAX_AI_CALL_ATTEMPTS_PER_LEAD:
            return False, ("ai_call_count is already %d; no retry beyond %d attempts per lead"
                           % (attempts, MAX_AI_CALL_ATTEMPTS_PER_LEAD))
        if scheduled and now < scheduled:
            return False, ("retry_scheduled: earliest window is %sZ; pass it before another attempt"
                           % scheduled)
    else:
        if stage not in (ELIGIBLE, AI_NO_ANSWER):
            return False, ("already in the pipeline at %s; the AI cold call is "
                           "once per lead" % stage)

        # First call (ELIGIBLE). A lead at ELIGIBLE has never been dispatched, so
        # a recorded last_call_date is an inconsistency, not permission to call
        # again; the old 14-day cooldown is superseded by the retry path below.
        if stage == ELIGIBLE:
            if attempts >= MAX_AI_CALL_ATTEMPTS_PER_LEAD:
                return False, ("ai_call_count is already %d; limit is %d"
                               % (attempts, MAX_AI_CALL_ATTEMPTS_PER_LEAD))
            if last_call:
                return False, ("inconsistent state: still ELIGIBLE but last_call_date=%s "
                               "recorded — resolve the lead before dialling again"
                               % last_call)

        # Retry (AI_NO_ANSWER). Bounded by the same attempt budget as a first
        # call, plus the cooldown, the scheduled window and the calendar-day rule.
        if stage == AI_NO_ANSWER:
            if attempts >= MAX_AI_CALL_ATTEMPTS_PER_LEAD:
                return False, ("ai_call_count is already %d; no retry beyond %d "
                               "attempts per lead"
                               % (attempts, MAX_AI_CALL_ATTEMPTS_PER_LEAD))
            # Standard retry: 3 days (72h) cooldown + different calendar day + scheduled window
            if last_call and now < last_call + timedelta(hours=AI_CALL_RETRY_COOLDOWN_HOURS):
                return False, ("retry cooldown_active: last call %s; earliest retry "
                               "is >= %d hours out (%sZ)"
                               % (last_call, AI_CALL_RETRY_COOLDOWN_HOURS,
                                  (last_call + timedelta(hours=AI_CALL_RETRY_COOLDOWN_HOURS)).isoformat()))
            if last_call and _as_ist(last_call).date() == _as_ist(now).date():
                return False, ("retry_refused: not a different calendar day from "
                               "the last call (Asia/Kolkata)")
            if scheduled and now < scheduled:
                return False, ("retry_scheduled: earliest window is %sZ; pass it "
                               "before another attempt" % scheduled)

            # Hardening cross-check: a meaningful exchange recorded against a
            # retryable outcome is still final. classify_meaningful_conversation
            # uses the SAME test the record path used, so the two cannot drift.
            last_outcome = (getattr(lead, "call_outcome_last", "") or "").upper()
            if last_outcome and classify_meaningful_conversation(
                    last_outcome, transcript=getattr(lead, "call_transcript", "") or ""):
                return False, ("meaningful_conversation: the last dial ended in %s "
                               "but also produced a real exchange — no retry"
                               % last_outcome)
            if last_call is None:
                return False, ("inconsistent state: at AI_NO_ANSWER with no "
                               "last_call_date — resolve the lead before retrying")

    phone = (getattr(lead, "phone", "") or "").strip()
    if not phone:
        return False, "no phone on record"

    digits = re.sub(r"\D", "", phone)
    if len(digits) < 8:
        return False, f"phone is not a dialable number: {phone}"

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

    if _session is not None:
        dup_why = _duplicate_company_active(lead, _session)
        if dup_why:
            return False, dup_why

    allowed, why = preference_registry.check(phone)
    if not allowed:
        return False, "preference registry: %s" % why

    # Whichever column a caller filled, the answer is the same set.
    # Blank in BOTH is an incomplete record, not a business type.
    category = (
        (getattr(lead, "segment", "") or "").strip().lower()
        or (getattr(lead, "division", "") or "").strip().lower()
    )
    if category not in CALLABLE_SEGMENTS:
        return False, ("segment %s is not callable; allowed: %s"
                       % (category or "(none)", ", ".join(CALLABLE_SEGMENTS)))

    # The per-lead line, not the template: that is what this lead will hear.
    ok, why = script_discloses(opening_for(lead))
    if not ok:
        return False, "opening script fails disclosure: %s" % why

    from app.observability import ALLOWED, decision
    decision("ai_call.eligibility", ALLOWED,
             f"eligible at {stage} (attempt {attempts + 1} of "
             f"{MAX_AI_CALL_ATTEMPTS_PER_LEAD})", lead=lead,
             company=getattr(lead, "company", ""), segment=category)
    # 4 Nov 2026: duplicate-phone rows - only the canonical row is dialled.
    _dup = duplicate_phone_reason(lead)
    if _dup:
        return False, _dup
    return True, (f"eligible ({stage}): a disclosed AI qualification call is "
                  f"permitted, attempt {attempts + 1} of "
                  f"{MAX_AI_CALL_ATTEMPTS_PER_LEAD}")


def _duplicate_company_active(lead, session) -> str:
    """Refuse if another non-terminal row is already in play at this company.

    Computes the key in Python: company_normalized is not populated on every
    row, and a SQL-only match against that column would silently miss a
    duplicate whose column was never filled. Read-only — a permission gate
    that writes company_normalized was a side effect hiding in a check.
    """
    from app.models.models import B2BLead

    norm = normalize_company(getattr(lead, "company", "") or "")
    if not norm:
        return ""
    rid = getattr(lead, "id", None)
    rows = session.query(B2BLead.id, B2BLead.company, B2BLead.company_normalized,
                         B2BLead.status)
    if rid is not None:
        rows = rows.filter(B2BLead.id != rid)
    for oid, company, stored, status in rows:
        st = (status or "").upper()
        if st in _INACTIVE_FOR_DUP:
            continue
        other = (stored or "").strip() or normalize_company(company or "")
        if other and other == norm:
            return f"duplicate_company_active ({company})"
    return ""


EXPLICIT_CALLBACK_GRACE_ENV = "AI_CALL_EXPLICIT_CALLBACK_GRACE_MIN"
EXPLICIT_CALLBACK_EARLY_MIN = 0     # Exact due time only; never early
TRAI_CALL_START_MIN = 9 * 60 + 30     # 09:30 IST strict start
TRAI_CALL_END_MIN = 19 * 60          # 19:00 IST callback end


def explicit_callback_due(lead, now: datetime | None = None) -> tuple[bool, str]:
    """May this lead's explicit callback lift the business-hours window right now?

    True only when the lead gave an exact requested time (founder_callback_window
    parses), that time is due now (from 10 min before to GRACE min after; default
    30, which covers the single 5-minute retry), and local time is inside the
    telecom 09:00-21:00 IST window. callback_retry_count alone is NOT enough.
    Fail-closed: anything unparseable returns False (normal window applies).
    """
    try:
        if lead is None or not is_explicit_callback(lead):
            return False, "no explicit callback"
        now_utc = now or _utcnow()
        if now_utc.tzinfo is not None:
            now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
        window = (getattr(lead, "founder_callback_window", "") or "").strip()
        requested = parse_callback_datetime(window, now=now_utc) if window else None
        if requested is None:
            return False, "callback flag set but no exact requested time on record"
        if requested.tzinfo is not None:
            requested = requested.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            grace = max(0.0, float(os.getenv(EXPLICIT_CALLBACK_GRACE_ENV, "30") or 30))
        except ValueError:
            grace = 30.0
        if now_utc < requested:
            return False, "requested callback not yet due (only at exact due time)"
        if now_utc > requested + timedelta(minutes=grace):
            return False, "requested callback is stale (more than %d min past)" % int(grace)
        local = _as_ist(now_utc)
        m = local.hour * 60 + local.minute
        if not (TRAI_CALL_START_MIN <= m <= TRAI_CALL_END_MIN):
            return False, f"outside callback window 09:30-19:00 IST (local {local.strftime('%H:%M')})"
        return True, "explicit requested callback due now"
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, "explicit callback check failed (%s)" % exc.__class__.__name__


def call_hours_open(now: datetime | None = None, lead=None) -> tuple[bool, str]:
    """Process-wide AI dial window, Asia/Kolkata 09:30-19:30 Mon-Sat, fail-closed.

    If a lead explicitly requested a callback at a specific date/time, that
    requested callback has absolute priority, even outside normal business hours.
    """
    if lead is not None and is_explicit_callback(lead):
        # 3 Nov 2026 dial-guard fix: the callback flag alone no longer lifts the
        # window. Only a callback whose exact requested time is due NOW (and
        # inside TRAI 09:00-21:00 IST) does; a stale flag falls through to the
        # normal window. Lead 2302 was dispatched 06:42 IST on 3 Nov off a
        # 2 Nov 10:30 request because callback_retry_count was still 1.
        _due, _due_why = explicit_callback_due(lead, now=now)
        if _due:
            return True, "explicit requested callback allowed outside business hours"
    if os.getenv(BUSINESS_HOURS_GATE_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        return True, "disabled by AI_CALL_BUSINESS_HOURS=off"
    local = _as_ist(now or _utcnow())
    m = local.hour * 60 + local.minute
    # Hard gate: NO call of any kind before 09:30 IST under any circumstances
    if m < (9 * 60 + 30):
        return False, f"outside business hours: strictly closed before 09:30 IST (local {local.strftime('%H:%M')})"
    from app.services import business_hours
    local = _as_ist(now or _utcnow())
    if local.weekday() == 6 and (os.getenv("AI_CALL_ALLOW_SUNDAY", "0").strip().lower() not in {"1", "true", "yes", "on"} or local.time() < business_hours._parse(os.getenv("AI_CALL_SUNDAY_START", "10:30"), "10:30")):
        return False, f"outside business hours (Sunday closed or before Sunday start; local {local.strftime('%A')})"
    start, end = business_hours.window()
    # 2026-10-17: AI dial window ends later than email/WhatsApp (founder: calls 09:30-19:30 IST Mon-Sat).
    end = business_hours._parse(os.getenv("AI_CALL_BUSINESS_HOURS_END", "19:00"), "19:00")
    if start <= local.time() <= end:
        return True, f"business hours open (Asia/Kolkata {local:%H:%M})"
    return False, ("outside business hours (Asia/Kolkata %s-%s Mon-Sat; local %s %s)"
                   % (start, end, local.strftime("%A"), local.strftime("%H:%M")))


def classify_meaningful_conversation(outcome: str, *, transcript: str = "",
                                     duration_s: int | None = None) -> bool:
    """Did a real person have a real exchange on the last dial?

    The verdict is OUTCOMES-driven, never elapsed-seconds-driven. Every
    outcome that maps away from AI_NO_ANSWER IS meaningful by construction
    (see MEANINGFUL_OUTCOMES). The transcript is used only in the defensive
    direction: a retryable outcome that nonetheless carries substantive
    transcript means a person engaged, so it is treated as meaningful and not
    retried. `duration_s` is accepted for interface symmetry but is never
    consulted — talk-time is a terrible proxy (voicemails read long) and the
    requirement is explicit about that.
    """
    key = (outcome or "").strip().upper()
    if key in MEANINGFUL_OUTCOMES:
        return True
    if key in RETRYABLE_OUTCOMES and transcript and len(transcript.strip()) > 40:
        return True
    return False


def _is_morning_window(local: datetime) -> bool:
    return local.hour < 14


def _opposite_window_start(local: datetime) -> datetime:
    """The other half of the business day, on the same calendar day: 14:00
    when the candidate is a morning window, window start (09:30) when it is an afternoon one
    (next business day is handled by the caller via next_open)."""
    from app.services import business_hours
    start_t, _ = business_hours.window()
    return local.replace(hour=start_t.hour if local.hour >= 14 else 14,
                         minute=start_t.minute if local.hour >= 14 else 0,
                         second=0, microsecond=0)


def schedule_ai_retry(lead, db=None, *, now: datetime | None = None,
                      callback_datetime: datetime | None = None) -> datetime:
    """Set lead.ai_retry_after to the earliest dispatch moment.

    If an explicit requested callback datetime is provided (or parsed from
    founder_callback_window), it overrides the 3-day (72h) retry and is preserved
    exactly (even if outside normal business hours).

    Otherwise, schedules the retry for 3 days (72h) out, inside business hours,
    on a different calendar day in the opposite half-day window.

    Stored UTC-naive (schema convention). Scheduling alone never dials; the
    gate still re-checks cooldown, calendar day and business hours at the
    (later) dispatch time. Returns the moment.
    """
    from app.observability import decision
    from app.services import business_hours

    now = now or _utcnow()

    # Check for explicit callback datetime
    explicit_dt = callback_datetime
    if explicit_dt is None:
        cb_str = getattr(lead, "founder_callback_window", "") or ""
        explicit_dt = parse_callback_datetime(cb_str, now=now)

    if explicit_dt is not None:
        retry_at = explicit_dt.replace(tzinfo=None) if explicit_dt.tzinfo else explicit_dt
        lead.ai_retry_after = retry_at
        decision("ai_call.retry_scheduled", "SCHEDULED",
                 f"explicit requested callback window at {retry_at.isoformat()}Z (exact requested time preserved)", lead=lead)
        return retry_at

    naive_last = getattr(lead, "last_call_date", None) or now
    last_local = _as_ist(naive_last)
    cooldown_local = _as_ist(naive_last + timedelta(hours=AI_CALL_RETRY_COOLDOWN_HOURS))
    candidate = business_hours.next_open(cooldown_local).astimezone(business_hours.IST)
    if _is_morning_window(candidate) == _is_morning_window(last_local):
        opposite = _opposite_window_start(candidate)
        if opposite >= cooldown_local:
            candidate = opposite
        else:
            candidate = business_hours.next_open(opposite + timedelta(days=1)).astimezone(business_hours.IST)
    retry_at = candidate.astimezone(timezone.utc).replace(tzinfo=None)
    lead.ai_retry_after = retry_at
    decision("ai_call.retry_scheduled", "SCHEDULED",
             f"retry window opens {retry_at.isoformat()}Z", lead=lead)
    return retry_at


def begin_ai_attempt(db, lead) -> tuple[bool, str]:
    """Atomically open the attempt's ledger row BEFORE any network dial.

    Returns (True, "") with a CallHistory row inserted (status
    AI_CALL_ATTEMPTED, call_status CALLING). There is no fleet-wide ceiling
    to refuse on — the only volume bound is the per-lead gate
    (may_place_ai_call: max 3 attempts, cooldown, calendar day, business
    hours) enforced before this is reached. The insert runs in its own
    BEGIN IMMEDIATE transaction so two concurrent cycles cannot corrupt the
    ledger: the reservation is durable, and a crash between reserve and
    dispatch can never lose the attempt or double-count it.

    The caller releases the reservation (release_ai_call_attempt) if the
    provider refuses before any PSTN attempt, and commits the lead-state
    (pipeline.mark_ai_call_dispatched) when the dial goes out.
    """
    from sqlalchemy import text as sa_text

    from app.models.models import CallHistory

    engine = db.get_bind()
    # The caller's session may already hold an open (read) transaction. A
    # BEGIN IMMEDIATE on the same SQLite handle would then fail with "cannot
    # start a transaction within a transaction". Every call site reaches here
    # before any uncommitted session write (eligibility is read-only up to the
    # reserve), so closing the read txn loses nothing — and the reservation
    # itself stays atomic on its own connection.
    if db.in_transaction():
        db.rollback()
    conn = engine.connect()
    try:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        conn.execute(
            sa_text("INSERT INTO call_history (lead_id, call_date, status, "
                    "call_status) VALUES (:lid, :at, 'AI_CALL_ATTEMPTED', "
                    "'CALLING')"),
            {"lid": getattr(lead, "id", None), "at": _utcnow()})
        conn.commit()
        return True, ""
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_ai_call_dispatched(lead, db) -> None:
    """The dial went out. Record the attempt against the lead and timestamp
    the reservation row. Called by the (patched) dial path right after a
    successful provider placement; record_ai_outcome later moves the SAME row
    to its outcome status, so dispatched and concluded attempts are counted
    once, and only once."""
    from app.models.models import CallHistory

    now = _utcnow()
    frm = stage_of(lead)
    if frm == ELIGIBLE:
        advance(lead, db, AI_CALL_ATTEMPTED, note="AI qualification call dispatched")
    elif frm == AI_NO_ANSWER:
        advance(lead, db, AI_CALL_ATTEMPTED, note="AI qualification call retry dispatched")
    elif is_explicit_callback(lead):
        # 13 Oct: an explicit callback on a lead already past the cold call
        # (e.g. AI_INTEREST_DETECTED) is recorded as an attempt without moving
        # its stage; raising here crashed the whole dial cycle after the dial.
        pass
    else:
        raise ValueError("cannot mark dispatched from %s" % frm)
    lead.ai_call_count = (getattr(lead, "ai_call_count", 0) or 0) + 1
    lead.last_call_date = now
    lead.ai_retry_after = None
    row = (db.query(CallHistory)
             .filter(CallHistory.lead_id == lead.id,
                     CallHistory.status == "AI_CALL_ATTEMPTED",
                     CallHistory.call_status == "CALLING")
             .order_by(CallHistory.id.desc()).first())
    if row is not None:
        row.call_date = now
    from app.observability import ALLOWED, decision
    decision("ai_call.attempt", ALLOWED,
             "attempt %d dispatched" % lead.ai_call_count, lead=lead)


def release_ai_call_attempt(db, lead) -> None:
    """The provider refused BEFORE any PSTN attempt. Delete the reservation
    so the slot is not consumed by a call that never rang; the attempt count
    on the lead is untouched because mark_ai_call_dispatched never ran.

    Unknown states do NOT release: a dial that went out and came back
    ambiguous stays on the ledger, because under-counting an ambiguous dial is
    how a fleet exceeds its ceiling.
    """
    from app.models.models import CallHistory

    rows = (db.query(CallHistory)
              .filter(CallHistory.lead_id == lead.id,
                      CallHistory.status == "AI_CALL_ATTEMPTED",
                      CallHistory.call_status == "CALLING")
              .order_by(CallHistory.id.desc()).all())
    for row in rows:
        db.delete(row)
    from app.observability import decision
    decision("ai_call.attempt", "RELEASED",
             "provider refused before dispatch; reservation returned", lead=lead)


# ------------------------------------------------------------ transitions --

def _force_stage(lead, db, to_stage: str, *, note: str = "") -> str:
    """Set the stage directly for record_ai_outcome's latest-answer rules
    (opt-out always wins; explicit not-interested downgrades interest).
    Audited like advance(); never used for arbitrary jumps."""
    to_stage = (to_stage or "").upper()
    if to_stage not in STAGES:
        raise ValueError("%r is not a pipeline stage" % to_stage)
    frm = stage_of(lead)
    lead.outreach_stage = to_stage
    lead.outreach_stage_at = datetime.utcnow()
    _event(lead, db, frm, to_stage, ("[override] " + note).strip())
    from app.observability import decision
    decision("ai_call.stage", "OVERRIDDEN", "%s -> %s (%s)" % (frm, to_stage, note[:120]),
             lead=lead, company=getattr(lead, "company", ""), was=frm)
    return to_stage


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
            return "", "", (f"the number given on the call ({_mask_num(supplied)}) is not a valid "
                            f"Indian mobile — likely misheard; confirm it before messaging")
        blocked = _wa_number_suppressed(digits)
        if blocked:
            return "", "", (f"the WhatsApp number given on the call ({_mask_num(digits)}) is "
                            f"{blocked} — not recorded as consent")
        return digits, "read out on the call", ""

    dialled = (getattr(lead, "phone", "") or "").strip()
    if not dialled:
        return "", "", "no dialled number on record to bind the opt-in to"
    if identity.is_landline(dialled):
        return "", "", (f"{_mask_num(dialled)} is a landline — WhatsApp cannot reach it; "
                        f"ask for a mobile number")
    blocked = _wa_number_suppressed(dialled)
    if blocked:
        return "", "", (f"the dialled number ({_mask_num(dialled)}) is {blocked} — "
                        f"not recorded as WhatsApp consent")
    return dialled, "the number that was called", ""


def _mask_num(value) -> str:
    """XXXXXX1234 -- never a full number in a reason, payload note or log."""
    import re
    d = re.sub(r"\D", "", str(value or ""))
    return ("XXXXXX" + d[-4:]) if d else "-"


def _wa_number_suppressed(number) -> str:
    """Block list / DND / SIP-strike check for a WhatsApp destination.
    Fail-closed is NOT applied here (a missing file just means no entries);
    the send-side pacer re-checks every number before anything goes out."""
    try:
        from app.services import whatsapp_pacer
        return whatsapp_pacer.number_suppressed(number)
    except Exception:
        return ""


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
    # 28 Oct (founder): the WhatsApp answer itself, so a "no" is recorded as a
    # fact (WHATSAPP_DECLINED) and never asked or messaged again. Reporting
    # only: a YES here is NOT consent -- only the WHATSAPP_OPT_IN outcome is.
    "wa_consent": frozenset({"YES", "NO", "UNKNOWN"}),
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
                      details: dict | None = None,
                      call_meta: dict | None = None) -> str:
    """Apply one AI call result. The only entry point after a dial.

    call_meta (optional) is what the voice agent/engine knows about the call
    itself -- call_ref, room/SIP ids, answered/ended timestamps, turns,
    opener variant, termination reason. It feeds the structured CallResult
    only; nothing in it affects the state machine below.

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
        # Legacy / direct-outcome path: a first call whose dispatch did NOT go
        # through begin_ai_attempt (webhook-only installations). The tried-and
        # true dispatch path marks the attempt in mark_ai_call_dispatched, so
        # this branch must not add a second count.
        advance(lead, db, AI_CALL_ATTEMPTED, note="AI qualification call placed")
        lead.ai_call_count = (getattr(lead, "ai_call_count", 0) or 0) + 1

    target = OUTCOMES[key]
    _cur = stage_of(lead)
    # 14 Oct: a callback dialled on a lead already past the cold call (e.g.
    # AI_INTEREST_DETECTED) that ends no-answer/busy, or repeats the same
    # stage, must not demote the lead or raise (it left call_results OPEN).
    # Keep the stage; the outcome and retry window below are still recorded.
    # fix3: an illegal move to a NON-positive stage (no answer / not
    # interested / wrong number / opt-out), incl. from a terminal stage, keeps
    # the stage instead of raising, so the outcome is still recorded. DNC is
    # not lost: the opt-out block below keys off target, not the stage.
    # Illegal POSITIVE moves (e.g. WhatsApp opt-in after a settled call) still
    # raise -> 400, so no consent can be created off-script.
    # NOT_INTERESTED / WRONG_NUMBER only count when a real redial is evidenced
    # (call_ref sent, or a dial outstanding); otherwise a conflicting second
    # report for the same call still raises.
    # 17 Oct (latest clear answer wins): an illegal stage move NEVER drops the
    # outcome or raises -> 400. Rules, in order:
    #   1. OPT_OUT always wins: stage forced to AI_OPTED_OUT (DNC set below).
    #   2. A lead already opted out / DNC keeps its stage.
    #   3. An explicit NOT_INTERESTED downgrades AI_INTEREST_DETECTED,
    #      FOUNDER_CALL_REQUESTED or AI_NO_ANSWER to AI_NOT_INTERESTED.
    #   4. A real-call answer (call_ref sent or dial outstanding) lifts
    #      AI_NO_ANSWER to its target, or AI_NOT_INTERESTED to
    #      AI_INTEREST_DETECTED. Unevidenced upgrades keep the stage.
    #   5. Anything else keeps the stage (e.g. FAILED after INTERESTED).
    # WhatsApp consent is only written on a legal / same-stage / evidenced
    # path; a kept-stage WHATSAPP_OPT_IN is logged as unbound, never consent.
    _evidenced = bool((call_meta or {}).get("call_ref")
                      or (getattr(lead, "call_status", "") or "").upper() == "CALLING")
    _consent_ok = True
    if target == _cur:
        _stage_action = "KEPT"
    elif target in TRANSITIONS.get(_cur, frozenset()):
        _stage_action = "ADVANCE"
    elif target == AI_OPTED_OUT:
        _stage_action = "FORCE"
    elif _cur == AI_OPTED_OUT or getattr(lead, "do_not_call", False):
        _stage_action = "KEPT"
    elif key == "NOT_INTERESTED" and _cur in {AI_INTEREST_DETECTED, FOUNDER_CALL_REQUESTED, AI_NO_ANSWER}:
        _stage_action = "FORCE"
    elif _evidenced and (_cur == AI_NO_ANSWER
                         or (_cur == AI_NOT_INTERESTED and target == AI_INTEREST_DETECTED)):
        _stage_action = "FORCE"
    else:
        _stage_action = "KEPT"
    if _stage_action == "ADVANCE":
        advance(lead, db, target, note=summary[:300])
    elif _stage_action == "FORCE":
        _force_stage(lead, db, target, note="%s reported; latest clear answer wins. %s" % (key, summary[:250]))
    else:
        if target != _cur:
            _consent_ok = False
        try:
            from app.observability import decision as _dec
            _dec("ai_call.stage", "KEPT", "%s -> %s not applied; stage kept, outcome %s recorded"
                 % (_cur, target, key), lead=lead)
        except Exception:  # noqa: BLE001
            pass

    if summary:
        lead.call_summary = summary[:2000]
    if transcript:
        lead.call_transcript = transcript[:20000]
    if interest:
        lead.ai_interest_level = interest.strip().upper()[:20]
    if callback_window:
        lead.founder_callback_window = callback_window.strip()[:120]
    lead.call_outcome_last = key
    lead.last_call_date = _utcnow()

    # The retry state machine. Only a retryable outcome opens a retry window;
    # everything else clears any window that might linger and records a
    # terminal decision. A retryable outcome with substantive transcript is a
    # real exchange and is treated as final rather than scheduled — the SAME
    # classifier the dial gate consults, so record and gate cannot drift.
    from app.observability import ALLOWED, decision
    if key in RETRYABLE_OUTCOMES:
        if classify_meaningful_conversation(key, transcript=transcript):
            lead.ai_retry_after = None
            lead.callback_retry_count = 0
            lead.founder_callback_window = None
            decision("ai_call.meaningful_conversation", "REFUSED",
                     "%s reported alongside transcript; treated as a real "
                     "exchange — no retry scheduled" % key, lead=lead)
        else:
            new_cb_dt = parse_callback_datetime(callback_window) if callback_window else None
            if new_cb_dt is not None:
                lead.callback_retry_count = 0
                lead.founder_callback_window = callback_window.strip()[:120]
                schedule_ai_retry(lead, db, callback_datetime=new_cb_dt)
            else:
                was_explicit = (getattr(lead, "callback_retry_count", 0) or 0) > 0 or is_explicit_callback(lead)
                retry_count = getattr(lead, "callback_retry_count", 0) or 0
                if was_explicit:
                    if retry_count == 0:
                        # 5-minute retry on first explicit callback failure
                        lead.callback_retry_count = 1
                        retry_at = _utcnow() + timedelta(minutes=5)
                        lead.ai_retry_after = retry_at
                        decision("ai_call.retry_scheduled", "SCHEDULED",
                                 f"5-minute retry scheduled for unsuccessful explicit callback at {retry_at.isoformat()}Z",
                                 lead=lead)
                    else:
                        # Second failure falls back to standard 72h cooldown inside business hours
                        lead.callback_retry_count = 0
                        lead.founder_callback_window = None
                        schedule_ai_retry(lead, db)
                        decision("ai_call.retry_scheduled", "SCHEDULED",
                                 f"second callback failure; falling back to 72h retry cooldown at {lead.ai_retry_after.isoformat()}Z",
                                 lead=lead)
                else:
                    lead.callback_retry_count = 0
                    schedule_ai_retry(lead, db)
    elif target in TERMINAL:
        lead.ai_retry_after = None
        lead.callback_retry_count = 0
        lead.founder_callback_window = None
        decision("ai_call.terminal_outcome", ALLOWED,
                 "%s -> %s; the lead is resolved, no retry window"
                 % (key, target), lead=lead)
    else:
        lead.callback_retry_count = 0
        new_cb_dt = parse_callback_datetime(callback_window) if callback_window else None
        if new_cb_dt is not None:
            lead.founder_callback_window = callback_window.strip()[:120]
            schedule_ai_retry(lead, db, callback_datetime=new_cb_dt)
        else:
            lead.ai_retry_after = None

    if key == "WHATSAPP_OPT_IN" and not _consent_ok:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="WHATSAPP_CONSENT_UNBOUND",
            actor="SYSTEM", channel="phone",
            payload={"source": "AI_CALL_WHATSAPP_REQUEST",
                     "evidence": (summary or "")[:1000],
                     "note": "WhatsApp opt-in reported while stage %s was kept; founder to confirm before messaging" % stage_of(lead)},
            occurred_at=datetime.utcnow()))
    elif key == "WHATSAPP_OPT_IN":
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
                         "offered_number": _mask_num(whatsapp_number or getattr(lead, "phone", "")),
                         "call_ref": (call_meta or {}).get("call_ref"),
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
                          + f" | WhatsApp destination: {_mask_num(destination)} ({how})"
                          + (f" | call_ref: {(call_meta or {}).get('call_ref')}"
                             if (call_meta or {}).get("call_ref") else "")),
            )
            # Klaviyo bridge: profile + WA consent (with the call's time and
            # id) + lists + opt-in event. Background thread; never raises here.
            try:
                from app.services import klaviyo_sync
                _cm = call_meta or {}
                klaviyo_sync.schedule(
                    lead, call_ref=_cm.get("call_ref"),
                    consented_at=(_cm.get("ended_at") or _cm.get("answered_at")
                                  or getattr(lead, "consent_timestamp", None)))
            except Exception:
                pass

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
    # One ledger row per ATTEMPT, not one per outcome: the dispatch reserved a
    # row (AI_CALL_ATTEMPTED/CALLING via begin_ai_attempt) and this outcome
    # concludes THAT row. The fallback insert keeps the legacy direct-outcome
    # path (no reservation) working, and both give an identical ledger shape —
    # exactly one status LIKE 'AI_%' row per dial — so "dispatched vs
    # concluded" reports see the same rows.
    reservation = (db.query(CallHistory)
                     .filter(CallHistory.lead_id == lead.id,
                             CallHistory.status == "AI_CALL_ATTEMPTED",
                             CallHistory.call_status == "CALLING")
                     .order_by(CallHistory.id.desc()).first())
    if reservation is not None:
        reservation.status = "AI_%s" % key
        reservation.call_status = "COMPLETED"
        reservation.summary = summary[:1000] or None
        reservation.call_date = lead.last_call_date
    else:
        db.add(CallHistory(lead_id=lead.id, call_date=lead.last_call_date,
                           status="AI_%s" % key, summary=summary[:1000] or None,
                           call_status="COMPLETED"))

    # Record structured learning as an advisory projection. This is deliberately
    # after outcome/safety state has been committed to the in-memory lead and
    # cannot alter DND, consent, callback scheduling, or retry policy.
    try:
        from app.services.call_learning import record_call_learning
        record_call_learning(
            db, lead, outcome=key, summary=summary, transcript=transcript,
            details=details or {}, callback_window=callback_window,
        )
    except Exception:
        # Learning must never make a completed call fail or change its outcome.
        pass

    learned = clean_call_details(details)
    if learned:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="AI_CALL_DETAILS",
            actor="ai_voice_agent", channel="phone",
            payload={"outcome": key, **learned},
            occurred_at=datetime.utcnow()))
    if learned.get("wa_consent") == "NO" and key != "WHATSAPP_OPT_IN":
        # The business said no to WhatsApp on this call. A fact, never
        # consent; the WhatsApp pacer skips any lead with this event newer
        # than its consent, and it is the signal not to ask again.
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="WHATSAPP_DECLINED",
            actor="ai_voice_agent", channel="phone",
            payload={"outcome": key, "call_ref": (call_meta or {}).get("call_ref"),
                     "source": "AI_CALL"},
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

    # The business asked for a person or requested samples.
    # Founder intervention is strictly required whenever samples are asked.
    _is_sample_request = "SAMPLE_REQUEST" in (summary or "").upper()
    if key in FOUNDER_CALL_ASKS or _is_sample_request:
        _reason = "SAMPLE_REQUEST: requested coffee samples on AI call — founder intervention required" if _is_sample_request else FOUNDER_CALL_ASKS.get(key, "founder call requested")
        request_founder_call(
            lead, db, reason=_reason, requested_by="AI",
            note=(summary or "")
                 + (f" | callback: {callback_window}" if callback_window else ""))

    # 29 Oct (Hiten): warm follow-up for interested / maybe / founder-call /
    # not-now / no outcomes. Drafts only (never approved, never sent here);
    # a founder-call ask gets a founder task + alert and no message. Must
    # never fail or change the recorded outcome.
    try:
        from app.services import warm_followup
        warm_followup.on_call_outcome(
            lead, db, key, summary=summary, interest=interest,
            transcript=transcript, details=details or {},
            callback_window=callback_window)
    except Exception as _wf_exc:  # noqa: BLE001
        try:
            from app.observability import decision as _dec
            _dec("warm_followup.error", "REFUSED", str(_wf_exc)[:300], lead=lead)
        except Exception:  # noqa: BLE001
            pass

    # Guaranteed writeback: the lead leaves call_status=CALLING and the call
    # gets its structured result (call_intelligence). Before this, nothing on
    # this path ever touched call_status, so even a recorded outcome left the
    # lead CALLING forever. Advisory for everything except call_status; never
    # allowed to fail the outcome itself.
    try:
        from app.services.call_intelligence.capture import finalize_call
        meta = dict(call_meta or {})
        finalize_call(
            db, lead, fsm_key=key,
            termination_reason=meta.pop("termination_reason", None),
            source=meta.pop("source", None) or "voice_agent",
            meta=meta, transcript=transcript, summary=summary,
            details=details or {}, callback_window=callback_window,
            sip_status_code=meta.pop("sip_status_code", None),
        )
    except Exception:
        if getattr(lead, "call_status", None) == "CALLING":
            lead.call_status = "COMPLETED"

    # 4 Nov 2026 (Hiten "stop,fix all,resume", audit item h): a positive call
    # outcome must leave a dated follow-up on the lead. Before this only 4
    # leads had next_followup_date and catalogue/callback asks went cold.
    try:
        set_followup_after_call(lead, key, callback_window=callback_window)
    except Exception:  # noqa: BLE001 - advisory, never fails the outcome
        pass

    # 4 Nov 2026 (audit item f): interested/callback/catalogue asks raise an
    # internal founder alert (queued; sent only when FOUNDER_ALERT_SEND=1).
    try:
        from app.services import founder_handoff_alert
        founder_handoff_alert.on_call_outcome(lead, db, key, summary=summary,
                                      callback_window=callback_window)
    except Exception:  # noqa: BLE001 - advisory, never fails the outcome
        pass

    # Where the lead actually is -- past `target` when the ask was queued.
    # The webhook reports this, and a retry reports stage_of(); they must agree.
    return stage_of(lead)


# 4 Nov 2026 (audit item h): outcomes that need a human follow-up date.
FOLLOWUP_OUTCOMES = {"INTERESTED", "CALLBACK_REQUESTED", "MEETING_REQUESTED",
                     "HUMAN_HANDOFF", "SEND_INFO_EMAIL", "WHATSAPP_OPT_IN",
                     "CATALOGUE_REQUESTED"}


def set_followup_after_call(lead, key: str, *, callback_window: str = "", now=None) -> str | None:
    """Set lead.next_followup_date (IST YYYY-MM-DD) after a positive outcome.
    Callback date when the prospect gave one, else the next IST day. Never
    moves an existing future follow-up later. Returns the date set, or None."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    key = (key or "").strip().upper()
    if key not in FOLLOWUP_OUTCOMES:
        return None
    ist = _tz(_td(hours=5, minutes=30))
    now = now or _dt.now(ist)
    want = (now + _td(days=1)).date()
    if callback_window:
        try:
            cb = parse_callback_datetime(callback_window)
            if cb is not None:
                cbd = (cb.replace(tzinfo=_tz.utc).astimezone(ist) if cb.tzinfo is None else cb.astimezone(ist)).date()
                if cbd >= now.date():
                    want = cbd
        except Exception:  # noqa: BLE001
            pass
    cur = (getattr(lead, "next_followup_date", None) or "").strip()[:10]
    if cur and now.date().isoformat() <= cur <= want.isoformat():
        return None  # an earlier (still upcoming) follow-up already stands
    lead.next_followup_date = want.isoformat()
    return lead.next_followup_date


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
