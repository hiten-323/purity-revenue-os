"""
Phone intelligence — the call queue, and structured capture from calls.

The email channel reaches 21 businesses. The phone channel reaches 1428, and
860 of those have no website at all, so no crawler will ever reach them. The
website harvest measured a 3% yield on its remaining pool. A three-minute call
that ends with "send it to purchase@theirdomain" produces a FOUNDER_CALL-sourced
address, which the trust engine treats as business evidence — so one call can
create a verified, sendable contact that hours of crawling could not.

That is the loop this module closes: the phone is the only channel that can
MANUFACTURE email reachability rather than merely consume it.

EXTRACTION NEVER INVENTS
Everything here parses what the founder actually typed. If a note does not
mention a supplier, no supplier is recorded — not "unknown", not a guess. The
one rule this project has enforced everywhere applies hardest at the point
where a human's words become a database row.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# Category weights: what a single conversation is plausibly worth, given who
# they are. A distributor conversation carries downstream retailers with it;
# a single kirana does not. These rank the QUEUE — they are not revenue
# estimates and must never be presented as rupees.
CATEGORY_WEIGHT = {
    "distributor": 100, "wholesaler": 95, "institutional_buyer": 85,
    "modern_trade": 80, "grocery_chain": 80, "supermarket": 70,
    "hotel": 65, "corporate_office": 60, "manufacturing": 55,
    "restaurant": 50, "facility_management": 50, "hospital": 45,
    "college": 40, "school": 40, "cafe": 40, "retail_kirana": 25,
    "government": 30, "corporate_gifting": 55, "exporter": 70,
    "private_label": 75, "office_pantry": 45,
}

# What to actually ask, per category. A founder holding a phone needs the
# question, not a category label.
ASK = {
    "distributor": "which coffee brands they distribute, territory, and margin expectations",
    "wholesaler": "current coffee lines, monthly volume, and payment terms",
    "supermarket": "who decides shelf listing, and current instant coffee brands",
    "modern_trade": "listing process, category buyer name, and margin structure",
    "hotel": "monthly coffee consumption for rooms vs banquets, and current supplier",
    "restaurant": "monthly coffee usage and whether they buy retail or wholesale",
    "cafe": "current bean/instant split and who supplies them",
    "corporate_office": "pantry headcount, monthly consumption, and admin contact",
    "hospital": "canteen and staff pantry volumes, and procurement process",
    "institutional_buyer": "tender or rate-contract cycle and empanelment requirements",
    "manufacturing": "canteen headcount and current pantry supplier",
    "retail_kirana": "which instant coffee sells, and preferred pack size",
    "facility_management": "how many sites they service and who approves pantry supply",
}
DEFAULT_ASK = "who buys coffee, what they use now, and monthly volume"


def _now() -> datetime:
    return datetime.utcnow()


def classify_number(raw: str) -> dict:
    """
    Mobile, landline, or malformed — and it matters for more than tidiness.

    A first cut of this accepted only 10-digit numbers starting 6-9, which is
    the MOBILE rule, and flagged 194 landlines as invalid: +91-172-676-7777 is
    Chandigarh STD 172, and 0172 440 1234 is a hotel's front desk. Those are
    perfectly callable, and they belong to hotels and hospitals — exactly the
    high-value targets this queue exists to surface. An over-strict validator
    that silently discards good data is the recurring failure of this project.

    The distinction is operational: WhatsApp only works on mobiles, so the
    channel decision depends on it.
    """
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0091"):
        d = d[4:]
    elif d.startswith("91") and len(d) >= 12:
        d = d[2:]
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]

    if len(d) == 10 and d[0] in "6789":
        return {"kind": "mobile", "national": d, "whatsapp": True,
                "callable": True}
    # Landline: 2-4 digit STD code + 6-8 digit subscriber number.
    if 8 <= len(d) <= 11 and d[0] in "12345":
        return {"kind": "landline", "national": d, "whatsapp": False,
                "callable": True}
    return {"kind": "malformed", "national": d, "whatsapp": False,
            "callable": False, "why": f"{len(d)} digits, no valid Indian pattern"}


def call_priority(lead, db) -> dict:
    """
    Score one lead for calling. Highest value: businesses we CANNOT reach any
    other way, in categories where one conversation is worth the most.
    """
    from app.models.models import WorkflowEvent
    from app.services.trust_promoter import may_send

    div = (lead.division or "").lower()
    score = CATEGORY_WEIGHT.get(div, 35)
    reasons = [f"{div or 'uncategorised'} (weight {CATEGORY_WEIGHT.get(div, 35)})"]

    emailable = may_send(lead)[0]
    if not emailable:
        score += 40
        reasons.append("+40 not reachable by email — the call IS the channel")
    if not (lead.website or "").strip():
        score += 20
        reasons.append("+20 no website, so no crawler will ever find them")

    evs = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id).all()
    called = [e for e in evs if e.event_type in ("FOUNDER_CALL", "CALL_LOGGED")]
    if called:
        last = max((e.occurred_at for e in called if e.occurred_at), default=None)
        days = (_now() - last).days if last else 999
        if days < 14:
            score -= 60
            reasons.append(f"-60 called {days}d ago — let it breathe")
        else:
            reasons.append(f"called {days}d ago, due for follow-up")
    if any(e.event_type == "EMAIL_REPLY_RECEIVED" for e in evs):
        score += 50
        reasons.append("+50 they already replied — a call now converts")
    return {"score": score, "why": reasons, "previously_called": bool(called)}


def call_queue(db, limit: int = 20, city: str = "", category: str = "") -> list[dict]:
    """
    Who to call now, in order, with the context needed to dial immediately.

    The founder should not have to open a CRM record to make a call. Each row
    carries the number, what they are, what to ask, and why they are on the
    list at all.
    """
    from app.models.models import B2BLead
    from app.services.account_graph import account_for

    q = db.query(B2BLead).filter(B2BLead.phone != "", B2BLead.phone.isnot(None))
    if city:
        q = q.filter(B2BLead.city.ilike(f"%{city}%"))
    if category:
        q = q.filter(B2BLead.division == category)

    scored = []
    for l in q.all():
        num = classify_number(l.phone)
        if not num["callable"]:
            continue          # never put a number the founder cannot dial on the list
        p = call_priority(l, db)
        if p["score"] <= 0:
            continue
        scored.append((p["score"], l, p, num))
    scored.sort(key=lambda t: -t[0])

    out = []
    for score, l, p, num in scored[:limit]:
        div = (l.division or "").lower()
        out.append({
            "lead_id": l.id, "company": l.company, "city": l.city,
            "phone": l.phone, "category": div or "uncategorised",
            "number_kind": num["kind"], "whatsapp_possible": num["whatsapp"],
            "score": score,
            "ask": ASK.get(div, DEFAULT_ASK),
            "why_now": p["why"],
            "previously_called": p["previously_called"],
            "has_email": bool((l.email or "").strip()),
            "goal": ("get the procurement email — it is the only way this "
                     "business becomes reachable by mail")
                    if not (l.email or "").strip() else
                    "confirm the right person and their current supplier",
        })
    return out


# ── Structured capture ────────────────────────────────────────────────────
# Each pattern pulls a fact the founder actually stated. Anything not matched
# stays absent. None of these fill in a default.

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\b((?:\+91[\s-]?)?[6-9]\d{9})\b")
_VOLUME = re.compile(r"\b(\d{1,5}(?:\.\d+)?)\s*(kgs?|kilos?|jars?|packs?|"
                     r"cartons?|boxes|cases|tonnes?|tons?)\b", re.I)
_MONEY = re.compile(r"(?:rs\.?|inr|₹)\s?([\d,]+)", re.I)
_SUPPLIER = re.compile(
    r"\b(nescaf[eé]|nestl[eé]|bru|continental|levista|tata coffee|"
    r"third wave|blue tokai|sleepy owl|davidoff|moccona|cafe coffee day|ccd|"
    r"local roaster|unbranded)\b", re.I)
# The trigger phrase is matched case-insensitively; the NAME is not.
# Without the inline (?i:) this only matched a lower-case "spoke to", so
# every note beginning "Spoke to Mr Rajinder Singh" captured no person at
# all — the most valuable field on a call, silently missed on every call.
_PERSON = re.compile(
    r"(?i:spoke (?:to|with)|contact(?: is)?|owner(?: is)?|manager(?: is)?|"
    r"purchase(?:r)?(?: is)?|decision maker(?: is)?)[:\s]+"
    r"(?:(?i:mr|mrs|ms|shri)\.?\s*)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)")
_CALLBACK = re.compile(
    r"\b(?:call|callback|ring|revert)\b[^.\n]{0,25}?"
    r"\b(tomorrow|next week|next month|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|after \w+|\d{1,2}\s?(?:am|pm))\b", re.I)

# Outcomes the founder can record. Each maps to a real state change — these are
# not labels. Recording one is what moves the account, so the set is the
# commercial journey written down: who answered, what they gave us, what they
# asked for.
OUTCOMES = {
    # Could not have the conversation
    "NO_ANSWER": "did not pick up",
    "CALLBACK": "asked to be called later",
    "GATEKEEPER": "could not reach the decision maker",
    "WRONG_NUMBER": "not the business we thought",
    # The conversation produced an asset
    "DECISION_MAKER_FOUND": "identified who actually buys",
    "EMAIL_COLLECTED": "buyer gave an email address",
    "WHATSAPP_CONSENT": "buyer agreed to receive WhatsApp",
    # The conversation produced a request
    "CATALOGUE_REQUESTED": "asked for the catalogue",
    "SAMPLE_REQUESTED": "asked for a sample",
    "PRICING_REQUESTED": "asked for pricing or distributor terms",
    # The conversation reached a verdict
    "INTERESTED": "wants to proceed",
    "NOT_INTERESTED": "declined",
    "EXISTING_CONTRACT": "locked in with a supplier",
}

# Spellings the founder will actually type, and one retired name. Normalising
# beats rejecting: a call is logged once, minutes after it happened, and losing
# it to a ValueError over an underscore loses the only record of the call.
#
# SEND_DETAILS predates the split between "send the catalogue" and "send
# pricing" — they are different actions with different approval rules, so it
# resolves to the catalogue and pricing must be recorded explicitly.
_OUTCOME_ALIASES = {
    "CALL_BACK": "CALLBACK",
    "CALL BACK": "CALLBACK",
    "SEND_DETAILS": "CATALOGUE_REQUESTED",
    "CATALOG_REQUESTED": "CATALOGUE_REQUESTED",
    "WHATSAPP_CONSENT_GIVEN": "WHATSAPP_CONSENT",
    "DM_FOUND": "DECISION_MAKER_FOUND",
    "EMAIL_COLLECTED_": "EMAIL_COLLECTED",
    "PRICE_REQUESTED": "PRICING_REQUESTED",
    "NO_REPLY": "NO_ANSWER",
    "NOT_REACHABLE": "NO_ANSWER",

    # outreach_search.OUTCOMES speaks a second dialect — it is the registry the
    # API's call console posts against, and it grew separately. Mapping it in
    # here rather than leaving two vocabularies is the point: the founder logs
    # a call through either door and the same canonical outcome comes out.
    # outreach_search keeps its own labels and guidance text (presentation),
    # but no longer decides what an outcome MEANS.
    "BUSY": "NO_ANSWER",
    "CALL_LATER": "CALLBACK",
    "WRONG_PERSON": "GATEKEEPER",
    "SEND_WHATSAPP": "WHATSAPP_CONSENT",
    "SEND_CATALOGUE": "CATALOGUE_REQUESTED",
    "SEND_PRICING": "PRICING_REQUESTED",
    "PRICE_OBJECTION": "PRICING_REQUESTED",
    "MEETING_REQUESTED": "INTERESTED",
    "EXISTING_SUPPLIER": "EXISTING_CONTRACT",
    # DO_NOT_CONTACT is stronger than NOT_INTERESTED — it also sets
    # do_not_call. That flag is applied by apply_call_outcome; here it only
    # needs to resolve to the outcome that stops outreach.
    "DO_NOT_CONTACT": "NOT_INTERESTED",
    "OTHER": "NO_ANSWER",
}


def normalise_outcome(raw: str) -> str:
    """Founder input -> canonical outcome. Raises on anything unrecognised."""
    o = (raw or "").strip().upper().replace("-", "_")
    o = _OUTCOME_ALIASES.get(o, o)
    if o not in OUTCOMES:
        raise ValueError(f"unknown outcome {raw!r}; expected one of "
                         f"{', '.join(sorted(OUTCOMES))}")
    return o


# Every outcome leaves exactly one next action. A commitment is something the
# founder PROMISED on the call — the decision engine cannot infer it from
# stored state, so it outranks whatever the sequence had scheduled.
#
# EMAIL_COLLECTED deliberately has no commitment: collecting an address removes
# a blocker rather than creating an obligation, so the decision engine decides
# what to do with the newly-reachable contact. That is the whole point of one
# authority — this module reports what happened, it does not plan.
_COMMITMENT: dict[str, tuple[str | None, str, bool]] = {
    "CATALOGUE_REQUESTED":  ("SEND_CATALOGUE", "they asked for the catalogue", False),
    "SAMPLE_REQUESTED":     ("SEND_SAMPLE", "prepare a sample dispatch", False),
    "PRICING_REQUESTED":    ("FOUNDER_PRICING", "pricing and terms are founder-only", True),
    "INTERESTED":           ("FOUNDER_CALL", "buying conversation — founder call", True),
    "WHATSAPP_CONSENT":     ("SEND_WHATSAPP", "consent given — WhatsApp now permitted", False),
    "DECISION_MAKER_FOUND": ("CALL_DECISION_MAKER", "call the named decision maker", False),
    "CALLBACK":             ("SCHEDULE_CALLBACK", "call back {when}", False),
    "GATEKEEPER":           ("CALL_AGAIN", "try again for the decision maker", False),
    "NO_ANSWER":            ("CALL_AGAIN", "no answer — try again", False),
    "NOT_INTERESTED":       ("NONE", "closed — no follow-up", False),
    "WRONG_NUMBER":         ("NONE", "number cleared — not this business", False),
    # Was ("NONE", "locked with a supplier — recycle later"). Nothing ever did
    # the recycling: NONE queues no action, the lead's status stays
    # DISCOVERED, and it fell back into the anonymous pool as if the call had
    # never happened -- breaking this table's own rule above ("every outcome
    # leaves exactly one next action") on the single most common objection in
    # B2B buying. It is also not what the founder's playbook says: the
    # EXISTING_SUPPLIER entry in outreach_search.OUTCOMES reads "Not a
    # rejection. Ask what they pay and what they would change; offer a
    # comparison rather than a switch." A scheduled callback is that
    # recycle, actually scheduled; the delay comes from that registry entry.
    "EXISTING_CONTRACT":    ("SCHEDULE_CALLBACK",
                             "has a supplier — call back {when} with a side-by-side "
                             "comparison, not a switch pitch", False),
    "EMAIL_COLLECTED":      (None, "", False),
}

# Actions nobody but the founder may execute. Money is founder-only: an
# automated system that quotes a price has committed the company to it.
FOUNDER_ONLY = {"FOUNDER_PRICING", "FOUNDER_CALL"}


def extract(notes: str) -> dict:
    """Pull only facts literally present in the founder's notes."""
    t = notes or ""
    out: dict = {}
    if (m := _EMAIL.search(t)):
        # Strip sentence punctuation the pattern picks up: a note ending
        # "...send it to purchase@acme.com." yielded "purchase@acme.com." and
        # would have been stored, verified and mailed as a broken address.
        out["email"] = m.group(0).lower().rstrip(".,;:)]}'\"")
    if (m := _SUPPLIER.search(t)):
        out["current_supplier"] = m.group(0).title()
    if (m := _PERSON.search(t)):
        out["contact_name"] = m.group(1).strip()
    if (m := _CALLBACK.search(t)):
        out["callback"] = m.group(1)
    vols = _VOLUME.findall(t)
    if vols:
        out["volumes"] = [f"{n} {u.lower()}" for n, u in vols]
    money = _MONEY.findall(t)
    if money:
        out["amounts"] = [m.replace(",", "") for m in money]
    phones = [p for p in _PHONE.findall(t)]
    if phones:
        out["phones_mentioned"] = sorted(set(phones))
    return out


def log_call(lead, db, outcome: str, notes: str = "",
             duration_min: int | None = None) -> dict:
    """
    Record a call and apply what it taught us.

    The single most valuable thing a call produces is an email address with
    FOUNDER_CALL provenance: the founder heard it from the buyer, which is
    stronger evidence than anything a crawler can produce, and it makes an
    otherwise unreachable business emailable.
    """
    from app.models.models import WorkflowEvent
    from app.services import trust_promoter as tp

    outcome = normalise_outcome(outcome)
    facts = extract(notes)
    applied = []

    call_payload = {"outcome": outcome, "meaning": OUTCOMES[outcome],
                    "notes": (notes or "")[:1500], "duration_min": duration_min,
                    "extracted": facts, "phone": lead.phone}
    call_event = WorkflowEvent(
        lead_id=lead.id, event_type="FOUNDER_CALL", actor="FOUNDER",
        channel="phone", payload=call_payload, occurred_at=_now())
    db.add(call_event)

    # An address the buyer gave the founder on a call.
    got_email = facts.get("email")
    if got_email and got_email != (lead.email or "").lower():
        previous = lead.email
        lead.email = got_email
        tp.on_founder_call(lead, db, confirmed=True,
                           notes=f"address given on a call: {got_email}")
        applied.append(f"email {previous or '(none)'} -> {got_email} (VERIFIED)")

    if facts.get("contact_name") and not (lead.contact_name or "").strip():
        lead.contact_name = facts["contact_name"]
        applied.append(f"contact_name = {facts['contact_name']}")

    for key in ("current_supplier", "volumes", "amounts", "callback"):
        if facts.get(key):
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="BUSINESS_FACT_LEARNED",
                actor="FOUNDER", channel="phone",
                payload={"fact": key, "value": facts[key],
                         "source": "founder call", "observed_at": _now().isoformat()},
                occurred_at=_now()))
            applied.append(f"learned {key} = {facts[key]}")

    # Outcomes that change commercial state.
    if outcome == "SAMPLE_REQUESTED":
        tp.on_business_event(lead, db, "sample_request", {"via": "phone"})
        applied.append("account -> TRUSTED (sample requested)")
    elif outcome == "NOT_INTERESTED":
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="LEAD_DISQUALIFIED", actor="FOUNDER",
            channel="phone", payload={"reason": "declined on a call"},
            occurred_at=_now()))
        applied.append("marked declined")
    elif outcome == "WRONG_NUMBER":
        # Shared with outreach_search.apply_call_outcome, so both doors clear
        # the same fields -- including a whatsapp_number holding the same
        # digits, which Power Hour would otherwise dial again -- and both
        # record the cleared value, which contact_enricher checks before it
        # writes a number back.
        from app.services.outreach_search import clear_wrong_number
        cleared = clear_wrong_number(lead)
        if cleared:
            # Reassigned, not mutated: SQLAlchemy only sees a new JSON value.
            call_event.payload = {**call_payload, "cleared_numbers": cleared}
        applied.append("phone cleared — not this business")

    elif outcome == "WHATSAPP_CONSENT":
        # The outcome that unlocks AiSensy. Meta requires opt-in before any
        # business-initiated WhatsApp, and a buyer saying "yes, send it on
        # WhatsApp" to the founder IS that opt-in — it just has to be written
        # to the field the consent gate actually reads.
        #
        # EXPLICIT and this exact source shape are what whatsapp_sender's
        # consent_check treats as permission; writing anything else here would
        # record consent that the sender still refuses to act on.
        from app.services.whatsapp_consent import record as record_whatsapp_consent
        mentioned = facts.get("phones_mentioned") or []
        if len(mentioned) > 1:
            raise ValueError("WHATSAPP_CONSENT note contains multiple phone numbers; record one WhatsApp destination")
        if mentioned:
            lead.whatsapp_number = mentioned[0]
        consent_result = record_whatsapp_consent(
            lead, db, source="FOUNDER_CALL", evidence=(notes or "")[:1000], create_next_action=False,
        )
        if consent_result.get("recorded"):
            applied.append(f"consent -> EXPLICIT (WhatsApp permitted for {consent_result['consent_phone']})")
        else:
            applied.append(f"WARNING: WhatsApp consent not recorded — {consent_result.get('reason')}")

    elif outcome == "EMAIL_COLLECTED":
        # The address is applied above by the shared extraction path. If the
        # founder picked this outcome and no address parsed out, the call's one
        # valuable product was lost — say so loudly rather than recording a
        # success that produced nothing.
        if not got_email:
            applied.append("WARNING: EMAIL_COLLECTED recorded but no address "
                           "found in the notes — re-open this call and add it")

    elif outcome == "DECISION_MAKER_FOUND":
        if facts.get("contact_name"):
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="DECISION_MAKER_IDENTIFIED",
                actor="FOUNDER", channel="phone",
                payload={"name": facts["contact_name"],
                         "notes": (notes or "")[:300]},
                occurred_at=_now()))
            applied.append(f"decision maker = {facts['contact_name']}")
        else:
            applied.append("WARNING: DECISION_MAKER_FOUND recorded but no name "
                           "found in the notes")

    elif outcome in ("CATALOGUE_REQUESTED", "PRICING_REQUESTED"):
        # Recorded as a commercial signal, NOT via on_business_event.
        #
        # on_business_event promotes email_trust to TRUSTED, and a request made
        # over the phone is no evidence whatsoever that the email address is
        # real. Routing it there would take a scraped info@ address from
        # DISCOVERED straight to sendable because a buyer asked for a catalogue
        # out loud. Trust in an address may only rise from evidence about that
        # address — which is why EMAIL_COLLECTED above goes through
        # on_founder_call: there, the founder actually heard it.
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="BUYING_SIGNAL", actor="FOUNDER",
            channel="phone",
            payload={"signal": outcome, "via": "phone",
                     "notes": (notes or "")[:300]},
            occurred_at=_now()))
        applied.append(f"buying signal recorded: {outcome}")

    # The founder presses Save and the system orchestrates the rest. Anything
    # that leaves the building still needs approval — what changes here is that
    # the founder no longer has to REMEMBER to queue it.
    next_action = _next_action_from_call(lead, db, outcome, facts, notes)
    db.commit()
    return {"outcome": outcome, "extracted": facts, "applied": applied,
            "next_action": next_action,
            "now_emailable": tp.may_send(lead)[0]}


# What the founder said -> what the system does about it.
_SAID = (
    (re.compile(r"\b(catalogue|catalog|product list|brochure)\b", re.I),
     "SEND_CATALOGUE", "queue a catalogue email for approval"),
    (re.compile(r"\b(pric|rate|quotation|quote|margin)\w*\b", re.I),
     "SEND_PRICING", "queue a pricing email for approval"),
    (re.compile(r"\b(sample|trial pack)\b", re.I),
     "SEND_SAMPLE", "prepare a sample dispatch"),
    (re.compile(r"\b(meet|visit|come over|appointment)\b", re.I),
     "BOOK_MEETING", "propose two slots"),
)


def _next_action_from_call(lead, db, outcome: str, facts: dict,
                           notes: str) -> dict:
    """
    Turn what was said into the one thing that should happen next.

    A call that ends with "send me the catalogue" and produces no queued
    catalogue is a lost deal that looked like a good conversation. This is the
    step where founder memory stops being load-bearing.

    This function does NOT plan. It splits every call into one of two cases:

      1. The call created a COMMITMENT — the buyer asked for something, or the
         founder promised something. That is new information no amount of
         stored state could reveal, so it becomes the next action directly.
      2. The call created no commitment (EMAIL_COLLECTED: a blocker removed,
         nothing promised). Then decision_engine.evaluate_next_action decides,
         because it already weighs suppression, trust, account frequency,
         sequence position and the delivery guard.

    Deciding case 2 locally is what would make this a second decision engine —
    the exact duplication that produced six conflicting-answer bugs in this
    codebase. Every one surfaced as two modules disagreeing about one number.
    """
    from app.models.models import WorkflowEvent

    d = decide_after_call(lead, db, outcome, facts, notes)
    action, detail = d["action"], d["detail"]

    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="NEXT_ACTION_SET", actor="PHONE_INTELLIGENCE",
        channel="phone",
        payload={"action": action, "detail": detail, "from_outcome": outcome,
                 "blocked": d["blocked"], "due": facts.get("callback"),
                 "decided_by": d["decided_by"]},
        occurred_at=_now()))
    return d


def decide_after_call(lead, db, outcome: str, facts: dict | None = None,
                      notes: str = "") -> dict:
    """
    The decision half of "what happens after this call", with no side effects.

    Both call-logging paths use this — phone_intelligence.log_call and
    outreach_search.apply_call_outcome — so a call logged through either door
    produces the same next action. They persist differently (WorkflowEvent vs
    LeadInteraction + ActionQueue), which is fine: two stores, one decision.
    Two DECISIONS is what caused the conflicting-answer bugs.
    """
    from app.services.decision_engine import evaluate_next_action
    from app.services.trust_promoter import may_send

    outcome = normalise_outcome(outcome)
    facts = facts or {}
    action, detail, decided_by = None, "", "call_commitment"

    commit = _COMMITMENT.get(outcome)
    if commit and commit[0]:
        action, detail = commit[0], commit[1]
        if action == "SCHEDULE_CALLBACK":
            detail = detail.format(when=facts.get("callback") or "as agreed")

        # INTERESTED is the one deliberately vague outcome — "wants to proceed"
        # does not say proceed with WHAT. If the notes name something concrete,
        # that is more actionable than a generic founder call.
        if outcome == "INTERESTED":
            for rx, act, desc in _SAID:
                if rx.search(notes or ""):
                    action, detail, decided_by = act, desc, "call_commitment+notes"
                    break
    else:
        # No commitment: the engine owns this decision.
        d = evaluate_next_action(lead, db)
        decided_by = "evaluate_next_action"
        action = {"SEND": "SEND_INTRO", "DRAFT_ONLY": "DRAFT_FOR_APPROVAL",
                  "FOUNDER_REVIEW": "FOUNDER_CALL", "ENRICH": "ENRICH",
                  "WAIT": "WAIT", "NONE": "NONE",
                  "SUPPRESS": "NONE"}.get(d["action"], d["action"])
        detail = d["reason"]

    # No try/except around may_send. If the trust gate cannot answer, that is a
    # bug worth seeing, not a reason to guess — and guessing False would
    # silently mark every follow-up BLOCKED, while guessing True would queue
    # sends to addresses nobody vouched for.
    emailable = may_send(lead)[0]

    # An action needs the channel it rides on to actually be usable. If the call
    # did not produce one, say so plainly rather than queueing something that
    # cannot be sent — a queue full of impossible sends is what stalled the send
    # queue for four hours earlier today.
    blocked = None
    if action in ("SEND_CATALOGUE", "SEND_PRICING", "SEND_INTRO") and not emailable:
        blocked = "no sendable address — get the email before this can go out"
    elif action == "SEND_WHATSAPP":
        try:
            from app.services.whatsapp_sender import consent_check
            ok, why = consent_check(lead)
            if not ok:
                blocked = f"WhatsApp not permitted: {why}"
        except Exception as e:                       # adapter missing entirely
            blocked = f"WhatsApp transport unavailable ({e.__class__.__name__})"

    return {"action": action, "detail": detail, "blocked": blocked,
            "decided_by": decided_by, "outcome": outcome,
            "founder_only": action in FOUNDER_ONLY,
            "requires_approval": action in (
                "SEND_CATALOGUE", "SEND_PRICING", "SEND_SAMPLE", "SEND_INTRO",
                "SEND_WHATSAPP", "BOOK_MEETING", "DRAFT_FOR_APPROVAL")}


def call_funnel(db, days: int = 14) -> dict:
    """
    The only scoreboard that matters for the sprint.

    Calls -> conversations -> decision makers -> verified emails -> catalogues
    -> samples -> meetings -> orders. Whichever step drops hardest is what to
    fix next, and it will be based on commercial evidence rather than a guess
    about which channel works.
    """
    from app.models.models import WorkflowEvent

    since = _now() - timedelta(days=days)
    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.occurred_at >= since).all()

    calls = [e for e in evs if e.event_type == "FOUNDER_CALL"]

    def _canon(e):
        # The Power Hour door records its own dialect (BUSY, WRONG_PERSON,
        # OTHER...); log_call records canonical names. Comparing the raw
        # payload against canonical names counted a BUSY line, a referral
        # and an unclassified call as "conversations" -- inflating the one
        # scoreboard this docstring says decides where effort goes.
        raw = (e.payload or {}).get("outcome") or ""
        try:
            return normalise_outcome(raw)
        except ValueError:
            return raw

    reached = [e for e in calls
               if _canon(e) not in ("NO_ANSWER", "WRONG_NUMBER", "GATEKEEPER")]
    got_person = [e for e in calls
                  if (e.payload or {}).get("extracted", {}).get("contact_name")]
    got_email = [e for e in calls
                 if (e.payload or {}).get("extracted", {}).get("email")]
    samples = [e for e in calls if _canon(e) == "SAMPLE_REQUESTED"]
    catalogues = [e for e in evs if e.event_type == "NEXT_ACTION_SET"
                  and (e.payload or {}).get("action") == "SEND_CATALOGUE"]
    meetings = [e for e in evs if e.event_type == "MEETING_HELD"]
    orders = [e for e in evs if e.event_type == "ORDER_PLACED"]

    def pct(n, d):
        return f"{round(100 * n / d)}%" if d else "—"

    return {
        "window_days": days,
        "funnel": [
            {"stage": "calls made", "count": len(calls), "of_previous": "—"},
            {"stage": "conversations", "count": len(reached),
             "of_previous": pct(len(reached), len(calls))},
            {"stage": "decision maker named", "count": len(got_person),
             "of_previous": pct(len(got_person), len(reached))},
            {"stage": "verified email obtained", "count": len(got_email),
             "of_previous": pct(len(got_email), len(reached))},
            {"stage": "catalogue queued", "count": len(catalogues),
             "of_previous": pct(len(catalogues), len(got_email))},
            {"stage": "sample requested", "count": len(samples),
             "of_previous": pct(len(samples), len(reached))},
            {"stage": "meetings", "count": len(meetings), "of_previous": "—"},
            {"stage": "orders", "count": len(orders), "of_previous": "—"},
        ],
        # The channel comparison that decides where engineering effort goes.
        "vs_website_crawl": {
            "crawl": "623 sites -> 185 addresses -> 21 sendable (3% marginal)",
            "calls": f"{len(calls)} calls -> {len(got_email)} addresses",
            "note": "run 20 calls before drawing any conclusion",
        },
    }


def coverage(db) -> dict:
    """How much of the book each channel can actually reach."""
    from app.models.models import B2BLead
    from app.services.trust_promoter import may_send
    ls = db.query(B2BLead).all()
    phone = [l for l in ls if (getattr(l, "phone", "") or "").strip()]
    email = [l for l in ls if may_send(l)[0]]
    return {"leads": len(ls), "reachable_by_email": len(email),
            "reachable_by_phone": len(phone),
            "phone_only": len([l for l in phone
                               if not may_send(l)[0]]),
            "unreachable": len([l for l in ls
                                if not may_send(l)[0]
                                and not (getattr(l, "phone", "") or "").strip()])}


# Minimum denominator before a ratio means anything. Without this, one call
# that yields no email reads as "0% — CRITICAL" and would send the founder
# rewriting a script on the strength of a single conversation.
MIN_SAMPLE = 8

# What to actually do about each leak. Tied to the transition, not the stage,
# because a leak is always between two things.
REMEDY = {
    ("calls made", "conversations"):
        "too many unanswered — call in business hours, and try the landline "
        "where one exists",
    ("conversations", "decision maker named"):
        "reaching people but not the buyer — ask for the person who handles "
        "coffee purchasing in the first sentence, before explaining anything",
    ("decision maker named", "verified email obtained"):
        "talking to the right person but leaving without an address — ask for "
        "the procurement email before ending every call",
    ("verified email obtained", "catalogue queued"):
        "addresses collected but nothing sent — approve the queued catalogues",
    ("catalogue queued", "sample requested"):
        "catalogues going out without traction — the opening line or the "
        "subject is not landing; test one change at a time",
    ("sample requested", "meetings"):
        "samples requested but no meetings — call within 48 hours of delivery",
}


def bottleneck(db, days: int = 14) -> dict:
    """
    Which transition is leaking worst, and what to do about it.

    Reports the weakest ratio that has ENOUGH DATA to be a ratio at all.
    Everything upstream of MIN_SAMPLE returns "not enough data yet", which is
    the honest answer before the first sprint and the one that stops a founder
    changing a script because of one bad morning.
    """
    stages = call_funnel(db, days=days)["funnel"]
    worst, leaks = None, []
    for prev, cur in zip(stages, stages[1:]):
        denom, num = prev["count"], cur["count"]
        if denom < MIN_SAMPLE:
            continue
        rate = num / denom
        leak = {"from": prev["stage"], "to": cur["stage"],
                "denominator": denom, "numerator": num,
                "rate": f"{round(100 * rate)}%",
                "remedy": REMEDY.get((prev["stage"], cur["stage"]),
                                     "investigate this step")}
        leaks.append(leak)
        if worst is None or rate < worst["_rate"]:
            worst = {**leak, "_rate": rate}

    if worst is None:
        biggest = max((s["count"] for s in stages), default=0)
        return {"status": "not_enough_data",
                "message": (f"no stage has reached {MIN_SAMPLE} yet "
                            f"(largest is {biggest}) — make calls first"),
                "leaks": []}
    worst.pop("_rate", None)
    return {"status": "ok", "worst": worst, "leaks": leaks}


RECOVERY_HOUR_IST = 17          # 5 PM — enough day left to fix a zero
RECOVERY_LIST_SIZE = 10


def recovery_mode(db, buying_conversations_today: int, target: int = 2) -> dict:
    """
    No-Zero-Day. If it is late and the day has produced no buying conversation,
    stop offering choices and name the ten people most likely to give one.

    The failure this prevents is a day that felt busy — calls made, emails
    approved, records tidied — and created nothing. Discovery and admin always
    look available at 5 PM precisely because they are easy, and they are the
    two things that cannot rescue a zero.

    Ranking uses EVIDENCE, not prediction. With no orders on record there is no
    basis for "0.4 expected conversations", and a fabricated probability at the
    moment the founder is tired and behind is the worst possible time to guess.
    """
    from app.services.timeutil import to_ist

    now_ist = to_ist(_now())
    hour = now_ist.hour if now_ist else _now().hour
    if buying_conversations_today >= target:
        return {"active": False, "reason": "target met"}
    if hour < RECOVERY_HOUR_IST:
        return {"active": False,
                "reason": f"not yet {RECOVERY_HOUR_IST}:00 IST "
                          f"(now {hour:02d}:00) — normal priority still applies"}

    from app.models.models import B2BLead, WorkflowEvent

    scored = []
    for l in db.query(B2BLead).filter(B2BLead.phone != "",
                                      B2BLead.phone.isnot(None)).all():
        if not classify_number(l.phone)["callable"]:
            continue
        evs = db.query(WorkflowEvent).filter(
            WorkflowEvent.lead_id == l.id).all()
        types = {e.event_type for e in evs}

        # Warmest first, by what has actually happened with them.
        heat, why = 0, []
        if "EMAIL_REPLY_RECEIVED" in types:
            heat += 100
            why.append("has replied to us")
        if any(e.event_type == "FOUNDER_CALL"
               and (e.payload or {}).get("outcome") in
               ("INTERESTED", "SEND_DETAILS", "CALLBACK") for e in evs):
            heat += 70
            why.append("a previous call went somewhere")
        # Only count a send that actually names THIS company. Lead ids have
        # been recycled as the database was rebuilt, so historic EMAIL_SENT
        # events sit on rows that now belong to different businesses: "Basra
        # Traders" carried a send whose subject read "— More Supermarket", and
        # "Padam Traders" one addressed to Steel Authority of India. Trusting
        # that would have the founder open a recovery call with "following up
        # on my email" to someone who never received one.
        own = (l.company or "").strip().lower()
        genuine = [e for e in evs if e.event_type == "EMAIL_SENT"
                   and own and own[:18] in str((e.payload or {}).get(
                       "subject", "")).lower()]
        if genuine:
            heat += 30
            why.append("already contacted — they know the name")
        heat += CATEGORY_WEIGHT.get((l.division or "").lower(), 35) // 2
        if "LEAD_DISQUALIFIED" in types or "UNSUBSCRIBED" in types:
            continue                      # never resurface a closed door
        scored.append((heat, l, why))

    scored.sort(key=lambda t: -t[0])
    top = scored[:RECOVERY_LIST_SIZE]
    return {
        "active": True,
        "reason": (f"{buying_conversations_today}/{target} buying conversations "
                   f"at {hour:02d}:00 IST"),
        "instruction": ("call these in order until one asks for pricing, a "
                        "sample, or a meeting"),
        "hidden": ["discovery", "enrichment", "reports", "CRM cleanup"],
        "hidden_why": ("all of it is available and none of it can rescue a "
                       "zero — that is exactly why it is tempting now"),
        "call_list": [
            {"lead_id": l.id, "company": l.company, "city": l.city,
             "phone": l.phone, "category": (l.division or "").lower(),
             "heat": h, "why": why,
             "ask": ASK.get((l.division or "").lower(), DEFAULT_ASK)}
            for h, l, why in top],
        "exit_when": "one buying conversation, or the list is finished",
    }
