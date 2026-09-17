r"""
Which channel can actually reach this business, and what should happen next.

The policy
----------
Every qualified prospect enters a multi-channel programme. Channels are
attempted in sequence -- not all at once, not blindly -- until a reply, a
suppression, or the end of the programme. A channel is skipped when it cannot
lawfully or technically reach that particular business.

    Day 0   email       introduction
    Day 2   whatsapp    short, contextual
    Day 4   phone       one disclosed AI qualification call
    Day 6   email       different angle
    Day 8   whatsapp    follow-up
    Day 12  email       close the loop politely

LinkedIn is deliberately absent from the sequence. There is no adapter, and
automating connection requests violates LinkedIn's user agreement; a channel
that cannot be run without breaking terms is not a channel this system offers.
It is reported as ineligible with that reason rather than silently dropped.

This module PROPOSES. It does not send.
--------------------------------------
next_touch() returns what should happen. Executing it stays with the founder
approval queue and each channel's own sender, which is where suppression,
frequency caps, throttling and send-proof live. An orchestrator that dispatched
directly would be a seventh path around the chokepoint -- the exact defect
tender_auto_pricer had.

Every gate is IMPORTED, never restated
--------------------------------------
    email     trust_promoter.may_send          (trust AND confidence >= 40)
    whatsapp  whatsapp_sender.consent_check    (Meta opt-in vocabulary)
    phone     founder_call_pipeline.may_place_ai_call  (one call, DND scrub)

This codebase has produced the same bug seven times: a second copy of a rule
that drifts from the first, and the drift shows up as contact nobody agreed to.
A module whose whole purpose is to coordinate channels is the most tempting
place in the system to restate their rules. So it holds none of its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

EMAIL = "email"
WHATSAPP = "whatsapp"
PHONE = "phone"
LINKEDIN = "linkedin"
CHANNELS = (EMAIL, WHATSAPP, PHONE, LINKEDIN)

# (day offset, channel, angle) -- the angle names WHY this touch differs, so a
# second email is a different argument rather than the same pitch resent.
SEQUENCE = (
    (0, EMAIL, "introduction"),
    (2, WHATSAPP, "short_contextual"),
    (4, PHONE, "ai_qualification"),
    (6, EMAIL, "different_angle"),
    (8, WHATSAPP, "follow_up"),
    (12, EMAIL, "polite_close"),
)

# Engagement stops the sequence. These are LeadInteraction.outcome values that
# mean the BUSINESS actually spoke to us -- taken from the values the table
# really holds, not from a vocabulary invented here.
#
# BUSY, NO_ANSWER, CALL_LATER and GATEKEEPER are deliberately absent: nobody
# reached the buyer, so the sequence should continue. Treating "the receptionist
# answered" as engagement would silence outreach to every business with a front
# desk.
ENGAGED_OUTCOMES = (
    "INTERESTED", "NOT_INTERESTED", "MEETING_REQUESTED", "SAMPLE_REQUESTED",
    "PRICE_OBJECTION", "EXISTING_SUPPLIER", "SEND_DETAILS", "SEND_PRICING",
    "SEND_WHATSAPP", "DO_NOT_CONTACT", "WRONG_PERSON",
)

# Of those, the ones that must stop outreach permanently rather than hand over
# to a human.
SUPPRESSING_OUTCOMES = ("DO_NOT_CONTACT", "NOT_INTERESTED")


# ------------------------------------------------------------ eligibility --

def _email_ok(lead, db):
    from app.services.trust_promoter import may_send
    return may_send(lead)


def _whatsapp_ok(lead, db):
    """Needs a number and an opt-in. Verification is no longer askable.

    This gate used to require whatsapp_verified is True, on the principle
    that a mobile number is not a WhatsApp contact: the network is right and
    the account may simply not exist, so messages queue and never arrive.

    That principle is still true. What changed is that nobody can check any
    more. whatsapp_verified was set by Evolution's /chat/whatsappNumbers,
    which is a WhatsApp-Web (Baileys) capability. The transport is AiSensy on
    Meta's official platform now, and Meta deliberately exposes no
    "is this number on WhatsApp" endpoint -- enumerating its users is exactly
    what it will not allow. Baileys could answer it and is refused by name for
    reasons that have not changed.

    So the gate is narrowed rather than dropped, and the distinction is the
    one that was always stated: NULL is not False.

        True   verified, once, when the capability existed  -> allowed
        NULL   nobody ever asked, and nobody can now        -> allowed
        False  WhatsApp was asked and said no               -> refused

    Keeping NULL prohibitive would refuse all 1,307 leads permanently, which
    is not caution, it is a channel that can never open. The residual harm is
    small and bounded: a template sent to a number with no WhatsApp account is
    rejected by Meta rather than delivered, and a rejection generates no block
    and no report -- and blocks and reports are what actually move the quality
    rating this number's order flows depend on.
    """
    from app.services.whatsapp_sender import consent_check
    from app.services.identity import SHARED_PHONE_THRESHOLD
    from app.services.founder_call_pipeline import is_shared_across_many_leads

    number = (getattr(lead, "whatsapp_number", "") or "").strip()
    if not number:
        return False, "no WhatsApp-reachable number on record"

    if getattr(lead, "whatsapp_verified", None) is False:
        return False, "this number has no WhatsApp account (checked)"

    # See founder_call_pipeline.is_shared_across_many_leads — containment
    # for the 2026-09-15 phone-overwrite incident, not a fix for its
    # still-unknown cause. Checked here too because that incident's report
    # data showed the same placeholder value landing in whatsapp_number,
    # not just phone.
    if is_shared_across_many_leads(number, db, getattr(lead, "id", None)):
        return False, f"number is on record for {SHARED_PHONE_THRESHOLD}+ other leads — refusing to message it"

    return consent_check(lead)


def _phone_ok(lead, db):
    """Permission AND capability. They are different questions.

    may_place_ai_call answers "is this business allowed to receive the one
    cold qualification call" -- DND scrub, segment, per-lead and per-day caps.
    It says nothing about whether a call can actually be placed.

    A dry run over 150 candidates proposed phone for 145 of them, because
    email and WhatsApp are ineligible and the sequence falls through to the
    next channel. Every one of those proposals was unexecutable: no voice
    provider is configured. Proposing a touch that cannot happen fills the
    founder queue with work nobody can do and makes the system report
    readiness it does not have.

    So capability is asked of voice_router, which owns provider selection and
    refuses an unknown provider rather than guessing. Imported, not restated.
    An import failure is a refusal: a channel whose provider cannot be
    established has not been established.
    """
    from app.services.founder_call_pipeline import may_place_ai_call

    allowed, why = may_place_ai_call(lead)
    if not allowed:
        return False, why

    try:
        from app.services import voice_router
        ready, detail = voice_router.config_status()
    except Exception as exc:  # noqa: BLE001
        # Import failure and a raising status check are the same fact: the
        # provider's readiness cannot be established. Fail closed, exactly as
        # the email and WhatsApp gates do when their rule is unreachable.
        return False, (f"voice provider unavailable ({exc.__class__.__name__}); "
                       f"refusing to propose a call that cannot be placed")

    if not ready:
        return False, f"permitted, but no voice provider can place it: {detail}"
    return True, f"{why}; provider ready: {detail}"



def _linkedin_ok(lead, db):
    # Not "not built yet". Automating connection requests and messages breaks
    # LinkedIn's user agreement, and a channel that cannot be run without
    # breaking terms is not one this system will offer. Kept in CHANNELS so the
    # report says so out loud instead of leaving a silent gap.
    return False, ("no adapter, and automated LinkedIn outreach violates their "
                   "user agreement — treat as a manual channel")


GATES = {
    EMAIL: _email_ok,
    WHATSAPP: _whatsapp_ok,
    PHONE: _phone_ok,
    LINKEDIN: _linkedin_ok,
}


def eligibility(lead, db) -> dict:
    """Per channel: can we reach this business, and if not, why not.

    Reasons are the product here. "0 reachable" is not useful; "0 reachable
    because nobody has opted in to WhatsApp" tells you what to go and fix.
    """
    from app.observability import ALLOWED, REFUSED, decision

    out = {}
    for ch in CHANNELS:
        try:
            ok, why = GATES[ch](lead, db)
        except Exception as exc:  # noqa: BLE001 - an unreachable gate is a NO
            ok, why = False, (f"gate unavailable ({exc.__class__.__name__}); "
                              f"refusing — a channel whose rule cannot be "
                              f"reached has not satisfied it")
        # The reason logged is the one the channel's own gate returned. It is
        # not paraphrased here: a log that reads better than the code produced
        # is worse than none, because it will be believed.
        decision(f"{ch}.eligibility", ALLOWED if ok else REFUSED, why,
                 lead=lead, company=getattr(lead, "company", ""))
        out[ch] = {"eligible": bool(ok), "reason": why}
    return out


def reachable_channels(lead, db) -> list:
    return [c for c, v in eligibility(lead, db).items() if v["eligible"]]


# ------------------------------------------------------------- hard stops --

def stop_reason(lead, db) -> str:
    """Why automated outreach must not continue. Empty string means continue.

    Checked before any touch is proposed, because the cheapest wrong touch is
    the one sent to somebody who already answered.
    """
    if getattr(lead, "do_not_call", False):
        return "do_not_call is set"

    status = (getattr(lead, "consent_status", "") or "").upper()
    if status in ("OPT_OUT", "REFUSED", "UNSUBSCRIBED"):
        return f"consent_status={status} — permanently suppressed"

    from app.models.models import LeadInteraction
    engaged = (db.query(LeadInteraction)
                 .filter(LeadInteraction.lead_id == lead.id,
                         LeadInteraction.outcome.in_(ENGAGED_OUTCOMES))
                 .order_by(LeadInteraction.occurred_at.desc())
                 .first())
    if engaged:
        if engaged.outcome in SUPPRESSING_OUTCOMES:
            return f"the business said {engaged.outcome} — suppressed"
        return (f"the business engaged ({engaged.outcome}) — automated sequence "
                f"stops, a human takes it from here")

    stage = (getattr(lead, "outreach_stage", "") or "").upper()
    if stage in ("AI_OPTED_OUT", "AI_NOT_INTERESTED", "CLOSED_NO_FIT"):
        return f"pipeline stage {stage} is terminal"
    return ""


# --------------------------------------------------------------- planning --

def _touches_done(lead, db) -> set:
    """(day, channel) pairs already attempted, from the system's own records."""
    from app.models.models import WorkflowEvent
    done = set()
    evs = (db.query(WorkflowEvent)
             .filter(WorkflowEvent.lead_id == lead.id,
                     WorkflowEvent.event_type.in_(
                         ("EMAIL_SENT", "WHATSAPP_SENT", "FOUNDER_CALL_PIPELINE")))
             .all())
    for e in evs:
        ch = {"EMAIL_SENT": EMAIL, "WHATSAPP_SENT": WHATSAPP}.get(e.event_type)
        if ch:
            done.add((ch, e.occurred_at))
    if (getattr(lead, "ai_call_count", 0) or 0) > 0:
        done.add((PHONE, getattr(lead, "last_call_date", None)))
    return done


def next_touch(lead, db, *, started: datetime = None) -> dict:
    """The single next action, or an explanation of why there is none.

    Never returns a touch on an ineligible channel and never returns one after
    a stop. Executing what comes back is somebody else's job.
    """
    from app.observability import REFUSED, STOP, WAIT, decision

    stop = stop_reason(lead, db)
    if stop:
        decision("orchestrator.next_touch", STOP, stop, lead=lead)
        return {"action": "STOP", "reason": stop, "channel": None}

    elig = eligibility(lead, db)
    if not any(v["eligible"] for v in elig.values()):
        decision("orchestrator.next_touch", REFUSED,
                 "no channel can reach this business today", lead=lead,
                 blocked=",".join(sorted(elig)))
        # On the business's own record, with each channel's own reason. This
        # is the one an operator reads when asking "why has nothing happened
        # to this account?". Deduped, so a nightly sweep that reaches the same
        # conclusion does not write it again.
        from app.services import lead_journal as journal
        journal.record(
            lead, db, method=journal.ORCHESTRATOR, outcome=journal.NO_CHANNEL,
            remark="; ".join(f"{c}: {elig[c]['reason']}" for c in CHANNELS),
            by="orchestrator")
        return {"action": "UNREACHABLE", "channel": None,
                "reason": "no channel can lawfully or technically reach this "
                          "business today",
                "blocked_by": {c: v["reason"] for c, v in elig.items()}}

    started = started or getattr(lead, "stage_entered_date", None) or datetime.utcnow()
    age_days = max(0, (datetime.utcnow() - started).days)
    done = {c for c, _ in _touches_done(lead, db)}

    from app.observability import DONE, SKIPPED

    for day, channel, angle in SEQUENCE:
        if channel in done:
            decision(f"{channel}.sequence", SKIPPED, "already attempted on this lead",
                     lead=lead, day=day)
            continue                      # that channel has had its turn
        if not elig[channel]["eligible"]:
            decision(f"{channel}.sequence", SKIPPED, elig[channel]["reason"],
                     lead=lead, day=day)
            continue                      # skip, do not stall the sequence
        if age_days < day:
            decision("orchestrator.next_touch", WAIT,
                     f"next touch is {channel} on day {day}", lead=lead,
                     due_in_days=day - age_days, age_days=age_days)
            return {"action": "WAIT", "channel": channel, "angle": angle,
                    "due_in_days": day - age_days,
                    "reason": f"next touch is {channel} on day {day}"}
        decision("orchestrator.next_touch", "PROPOSE",
                 f"{channel}/{angle} is due (day {day}, lead is {age_days}d old) "
                 f"— awaiting founder approval", lead=lead)
        from app.services import lead_journal as journal
        journal.record(
            lead, db, method=channel, outcome=journal.QUEUED,
            remark=(f"day {day} of the sequence, angle '{angle}'. Eligible: "
                    f"{elig[channel]['reason']}. Awaiting founder approval — "
                    f"nothing has been sent."),
            by="orchestrator")
        return {"action": "PROPOSE", "channel": channel, "angle": angle,
                "day": day,
                "reason": elig[channel]["reason"],
                "note": "requires founder approval before it is sent"}

    decision("orchestrator.next_touch", DONE,
             "every eligible channel in the sequence has been attempted", lead=lead)
    return {"action": "COMPLETE", "channel": None,
            "reason": "every eligible channel in the sequence has been attempted"}


# ------------------------------------------------------------- reporting --

def reachability_report(db, limit: int = 0) -> dict:
    """How many businesses each channel can actually reach, and what blocks
    the rest. The denominator every conversion number needs."""
    from app.models.models import B2BLead
    from collections import Counter

    q = db.query(B2BLead)
    leads = q.limit(limit).all() if limit else q.all()

    counts = Counter()
    blockers = {c: Counter() for c in CHANNELS}
    reach_any = 0
    stopped = 0

    for l in leads:
        if stop_reason(l, db):
            stopped += 1
            continue
        elig = eligibility(l, db)
        any_ok = False
        for ch, v in elig.items():
            if v["eligible"]:
                counts[ch] += 1
                any_ok = True
            else:
                blockers[ch][str(v["reason"])[:70]] += 1
        reach_any += 1 if any_ok else 0

    return {
        "leads": len(leads),
        "suppressed_or_replied": stopped,
        "reachable_on_at_least_one_channel": reach_any,
        "by_channel": dict(counts),
        "top_blockers": {c: blockers[c].most_common(3) for c in CHANNELS},
    }


# ---------------------------------------------- WhatsApp, sent by a human --
#
# A DIFFERENT ACT FROM THE ONE _whatsapp_ok GOVERNS
#
# _whatsapp_ok decides whether the SYSTEM may send from the business's own
# WhatsApp Business number through the API. Meta's opt-in policy applies to
# that message, an unwanted one costs the number's quality rating, and the
# number is the one our email signature and packaging tell customers to call.
# So that gate demands a verified WhatsApp account and recorded consent, and
# today it passes zero of 1,858 leads.
#
# This gate decides whether the FOUNDER may open WhatsApp on their own phone
# and type a message. That is person-to-person contact. It needs no template,
# no Meta business verification, no BSP, and it cannot damage a rating that
# person-to-person messaging does not have.
#
# It is deliberately NOT a member of CHANNELS. next_touch() must never propose
# it, because the orchestrator plans automated touches and this one costs
# founder minutes -- the scarcest thing the business has. It is offered as a
# queue the founder pulls from, not work the system schedules for them.
#
# What it does NOT relax is suppression. "A human sent it by hand" is not an
# answer to a business that asked not to be contacted, so stop_reason() is the
# same authority here as for every automated channel.

WHATSAPP_MANUAL = "whatsapp_manual"


def manual_whatsapp_ok(lead, db) -> tuple:
    """May the founder message this business personally? (bool, reason)

    Never used to authorise an API send. whatsapp_sender.send_whatsapp()
    consults consent_check and does not import this function;
    test_whatsapp_manual_is_not_an_api_gate asserts that it never starts to.
    """
    stop = stop_reason(lead, db)
    if stop:
        return False, stop

    from app.services.identity import msisdn, SHARED_PHONE_THRESHOLD
    from app.services.founder_call_pipeline import is_shared_across_many_leads

    number = msisdn(getattr(lead, "whatsapp_number", "") or "")
    if not number:
        return False, "no WhatsApp-reachable number on record"
    if len(number) < 11:
        # msisdn() country-codes what it can. Anything still short is a
        # fragment, and a wa.me link built from a fragment opens a chat with
        # whoever does own those digits.
        return False, f"number is not dialable internationally ({number})"
    # See founder_call_pipeline.is_shared_across_many_leads — containment
    # for the 2026-09-15 phone-overwrite incident. This is the channel a
    # human founder actually acts on personally, so it gets the same guard
    # as the automated ones: opening a chat with a number 150+ unrelated
    # leads share is not messaging this business.
    if is_shared_across_many_leads(number, db, getattr(lead, "id", None)):
        return False, f"number is on record for {SHARED_PHONE_THRESHOLD}+ other leads — refusing to message it"

    return True, f"founder may message {number} personally"


def manual_whatsapp_queue(db, limit: int = 25, *, journal: bool = True) -> list:
    """The businesses worth the founder's next few WhatsApp minutes.

    Returns drafts, highest score first. Sends nothing and changes no lead
    status: the founder sends from their own phone and confirms afterwards,
    which is the only moment anyone actually knows a message left.

    The wa.me number comes from identity.msisdn, not from stripping
    non-digits here. Stripping produced ten-digit links for the 26 leads
    stored without a country code, and wa.me reads a ten-digit string as a
    US number -- a link that silently opens a chat with a stranger. That is
    the ninth time a local copy of "the digits that identify a subscriber"
    has drifted from the shared one.
    """
    from app.models.models import B2BLead
    from app.services import lead_journal
    from app.services.identity import msisdn
    import urllib.parse

    # Ranked by coffee_buying_score, NOT by score. `score` is 0 on all 1,308
    # businesses that have a WhatsApp number -- the 82 leads that carry a real
    # score have no number at all -- so ordering by it hands the founder an
    # arbitrary list. coffee_buying_score varies within segment (corporate
    # alone spans 0-80), which means it reflects something about the business
    # rather than just its label.
    #
    # estimated_value is deliberately not used: it holds three distinct values
    # across the whole table. It is a bucket wearing a rupee sign, and sorting
    # founder minutes by it would be sorting by nothing.
    candidates = (db.query(B2BLead)
                    .filter(B2BLead.whatsapp_number.isnot(None),
                            B2BLead.whatsapp_number != "")
                    .order_by(B2BLead.coffee_buying_score.desc().nullslast(),
                              B2BLead.final_score.desc().nullslast())
                    .limit(max(limit, 1) * 8)
                    .all())

    out = []
    for lead in candidates:
        ok, why = manual_whatsapp_ok(lead, db)
        if not ok:
            continue

        number = msisdn(lead.whatsapp_number)
        name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
        greeting = f"Hi {name}," if name else "Hi,"
        company = lead.company or "your team"
        segment = (getattr(lead, "segment", "") or "").lower()

        # Every claim below is in COMPANY_PROFILE. Nothing about turnover,
        # ISO, capacity, past government supply or client references appears
        # here -- none of it is verified, and a WhatsApp message from the
        # founder's own number is the least deniable place to overclaim.
        #
        # Two asks, because these are two different businesses. A cafe buys
        # coffee to serve; a distributor buys it to resell, and the number
        # that matters to them is the trade margin, not the tasting kit.
        if segment in ("distributor", "wholesaler"):
            ask = (f"Are you open to adding an instant coffee line at "
                   f"{company}? Trade margin is 28% plus 2% cash discount, "
                   f"and I can send samples before you decide.")
        else:
            ask = (f"Would a free tasting kit for {company} be useful? Happy "
                   f"to send one over and keep it to a 15-minute call after.")

        msg = (
            f"{greeting} Hiten here from Purity Beans, Abohar.\n\n"
            f"We supply 100% coffee -- zero chicory, no fillers -- to "
            f"corporates, hotels and distributors across India. FSSAI "
            f"licensed, PAN-India dispatch.\n\n"
            f"{ask}\n\n"
            f"Reply 1 = Sample  |  2 = Call  |  3 = Pricing\n"
            f"p3online.in"
        )

        out.append({
            "lead_id": lead.id,
            "company": lead.company,
            "city": getattr(lead, "city", None),
            "segment": getattr(lead, "segment", None),
            # The field the ranking actually used. Reporting `score` here
            # would show 0 beside every row and invite the founder to believe
            # the queue is unranked when it is ranked on something else.
            "coffee_buying_score": getattr(lead, "coffee_buying_score", None),
            "number": number,
            "message": msg,
            "whatsapp_url": f"https://wa.me/{number}?text={urllib.parse.quote(msg)}",
            "eligibility": why,
        })

        if journal:
            lead_journal.record(
                lead, db,
                method=lead_journal.WHATSAPP,
                outcome=lead_journal.QUEUED,
                remark=(f"drafted for founder-sent WhatsApp to {number}; "
                        f"not sent -- awaiting the founder's own send"),
                by="ORCHESTRATOR",
            )

        if len(out) >= limit:
            break

    if journal:
        try:
            db.commit()
        except Exception:
            db.rollback()

    return out
