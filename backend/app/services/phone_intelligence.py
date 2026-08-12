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

# Outcomes the founder can record. Each maps to a real state change.
OUTCOMES = {
    "INTERESTED": "wants to proceed",
    "SAMPLE_REQUESTED": "asked for a sample",
    "SEND_DETAILS": "asked for catalogue or pricing by email",
    "CALLBACK": "asked to be called later",
    "NOT_INTERESTED": "declined",
    "WRONG_NUMBER": "not the business we thought",
    "NO_ANSWER": "did not pick up",
    "GATEKEEPER": "could not reach the decision maker",
    "EXISTING_CONTRACT": "locked in with a supplier",
}


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

    outcome = (outcome or "").upper()
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of "
                         f"{', '.join(sorted(OUTCOMES))}")

    facts = extract(notes)
    applied = []

    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="FOUNDER_CALL", actor="FOUNDER",
        channel="phone",
        payload={"outcome": outcome, "meaning": OUTCOMES[outcome],
                 "notes": (notes or "")[:1500], "duration_min": duration_min,
                 "extracted": facts, "phone": lead.phone},
        occurred_at=_now()))

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
        lead.phone = ""
        applied.append("phone cleared — not this business")

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
    """
    from app.models.models import WorkflowEvent

    action, detail = None, ""
    if outcome == "SAMPLE_REQUESTED":
        action, detail = "SEND_SAMPLE", "prepare a sample dispatch"
    elif outcome == "CALLBACK":
        when = facts.get("callback") or "as agreed"
        action, detail = "SCHEDULE_CALLBACK", f"call back {when}"
    elif outcome in ("NOT_INTERESTED", "WRONG_NUMBER", "EXISTING_CONTRACT"):
        action, detail = "NONE", "closed — no follow-up"
    else:
        for rx, act, desc in _SAID:
            if rx.search(notes or ""):
                action, detail = act, desc
                break
    if action is None:
        action, detail = ("SEND_CATALOGUE", "they engaged — send the range") \
            if outcome in ("INTERESTED", "SEND_DETAILS") else \
            ("CALL_AGAIN", "no decision reached — try again")

    from app.services.trust_promoter import may_send
    # No try/except here. If the trust gate cannot answer, that is a bug worth
    # seeing, not a reason to guess — and guessing False would silently mark
    # every follow-up BLOCKED, while guessing True would queue sends to
    # addresses nobody vouched for.
    emailable = may_send(lead)[0]

    # An email action needs a sendable address. If the call did not produce
    # one, say so plainly rather than queueing something that cannot be sent —
    # a queue full of impossible sends is what stalled the send queue for four
    # hours earlier today.
    blocked = None
    if action in ("SEND_CATALOGUE", "SEND_PRICING") and not emailable:
        blocked = "no sendable address — get the email before this can go out"

    db.add(WorkflowEvent(
        lead_id=lead.id, event_type="NEXT_ACTION_SET", actor="PHONE_INTELLIGENCE",
        channel="phone",
        payload={"action": action, "detail": detail, "from_outcome": outcome,
                 "blocked": blocked, "due": facts.get("callback")},
        occurred_at=_now()))
    return {"action": action, "detail": detail, "blocked": blocked,
            "requires_approval": action in ("SEND_CATALOGUE", "SEND_PRICING",
                                            "SEND_SAMPLE", "BOOK_MEETING")}


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
    reached = [e for e in calls
               if (e.payload or {}).get("outcome") not in
               ("NO_ANSWER", "WRONG_NUMBER", "GATEKEEPER")]
    got_person = [e for e in calls
                  if (e.payload or {}).get("extracted", {}).get("contact_name")]
    got_email = [e for e in calls
                 if (e.payload or {}).get("extracted", {}).get("email")]
    samples = [e for e in calls
               if (e.payload or {}).get("outcome") == "SAMPLE_REQUESTED"]
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
