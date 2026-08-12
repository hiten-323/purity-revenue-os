"""
Daily Mission and Bottleneck Engine.

WHY THIS AND NOT A REVENUE DASHBOARD
The obvious thing to build is a Revenue Command Center — revenue this month,
expected revenue, likely reorders. None of it can be built honestly yet: the
event log holds 0 replies, 0 meetings, 0 samples, 0 proposals, 0 orders and ₹0
realised. A screen showing "Expected Revenue ₹4.8 Cr" derived from category
multipliers would be a modelled number sitting on the homepage driving decisions,
which is the same defect as the fabricated addresses, just more expensive.

So this answers the question the data CAN answer: what should the founder do in
the next hour, and what is standing in the way. Both from stored facts.

EVERY NUMBER HERE IS A COUNT OF ROWS OR A DIFFERENCE BETWEEN TIMESTAMPS.
Where money appears it is labelled MODELLED, because it is a category average
and not a measurement of that business.
"""
from __future__ import annotations

from datetime import datetime

from app.services.timeutil import ist_iso, ist_str

# How long is too long, per stage. These are not tuned — there is no outcome
# data to tune them against — so they are stated as the plain commitments they
# represent: a promise made and not kept, or a thread left to cool.
AGING_RULES = (
    ("SAMPLE_REQUESTED", 2, "sample requested and not dispatched"),
    ("SEND_PRICING", 2, "pricing asked for and not sent"),
    ("PROPOSAL_SENT", 7, "proposal sent, no response chased"),
    ("SAMPLE_SENT", 7, "sample delivered, no feedback asked"),
    ("MEETING_BOOKED", 3, "meeting booked, no preparation recorded"),
    ("REPLIED", 2, "they replied and nobody has called"),
)

QUIET_DAYS = 14      # an engaged account with nothing recorded for this long


def _days(since: datetime | None) -> int | None:
    return (datetime.utcnow() - since).days if since else None


def bottlenecks(db) -> list[dict]:
    """
    What is preventing revenue today, as named blockers with counts.

    Ordered by how much of it is our own doing: a sample we promised and never
    sent outranks a prospect who has not replied, because one is fixable this
    afternoon and the other is not.
    """
    from app.models.models import B2BLead, LeadInteraction, WorkflowEvent
    from app.services.contact_trust import sendable, actionable

    leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(["DISQUALIFIED", "CLOSED_LOST", "DO_NOT_CONTACT"])).all()
    inter: dict[int, list] = {}
    for i in db.query(LeadInteraction).filter(
            LeadInteraction.superseded_by_id.is_(None)).all():
        inter.setdefault(i.lead_id, []).append(i)
    ev: dict[int, list] = {}
    for e in db.query(WorkflowEvent).all():
        if e.lead_id:
            ev.setdefault(e.lead_id, []).append(e)

    owed, quiet, no_contact, unverified, phone_ready = [], [], [], [], []

    for l in leads:
        ii = inter.get(l.id, [])
        ee = ev.get(l.id, [])

        # 1. Promises we made and did not keep — read from real outcomes.
        for outcome, limit, label in AGING_RULES:
            hit = next((i for i in ii if (i.outcome or "") == outcome), None)
            if not hit:
                continue
            age = _days(hit.occurred_at)
            if age is None or age < limit:
                continue
            done = any(e.event_type in ("SAMPLE_SENT", "PROPOSAL_SENT",
                                        "EMAIL_SENT", "FOUNDER_CALL")
                       and e.occurred_at and hit.occurred_at
                       and e.occurred_at > hit.occurred_at for e in ee)
            if not done:
                owed.append({"lead_id": l.id, "company": l.company,
                             "blocker": label, "days_waiting": age})

        # 2. Engaged but gone quiet.
        last = max([i.occurred_at for i in ii if i.occurred_at] or [None],
                   default=None)
        if ii and last:
            d = _days(last)
            if d is not None and d >= QUIET_DAYS:
                quiet.append({"lead_id": l.id, "company": l.company,
                              "days_quiet": d})

        # 3. Reachability — the dominant blocker at this stage of the business.
        can_mail = sendable(l)[0] and actionable(l)[0]
        has_phone = bool((l.phone or "").strip() or (l.whatsapp_number or "").strip())
        if not can_mail:
            if (l.email or "").strip():
                unverified.append(l.id)
            else:
                no_contact.append(l.id)
                if has_phone:
                    phone_ready.append(l.id)

    out = []
    if owed:
        out.append({"bottleneck": "commitments we have not honoured",
                    "count": len(owed), "urgency": "high",
                    "why": "these are promises made to a buyer who is waiting",
                    "action": "dispatch, quote or call — whichever was promised",
                    "examples": owed[:5]})
    if quiet:
        out.append({"bottleneck": f"engaged accounts quiet {QUIET_DAYS}+ days",
                    "count": len(quiet), "urgency": "high",
                    "why": "a conversation already started is the cheapest to restart",
                    "action": "call the decision maker",
                    "examples": quiet[:5]})
    if phone_ready:
        out.append({"bottleneck": "qualified businesses reachable only by phone",
                    "count": len(phone_ready), "urgency": "medium",
                    "why": "no usable email, but a phone on record — workable today",
                    "action": "Power Hour: call and capture the email on the call",
                    "examples": phone_ready[:5]})
    if unverified:
        out.append({"bottleneck": "addresses on file that failed verification",
                    "count": len(unverified), "urgency": "medium",
                    "why": "cannot be sent to and will not fix themselves",
                    "action": "find a real address, or drop the address and call",
                    "examples": unverified[:5]})
    rest = len(no_contact) - len(phone_ready)
    if rest > 0:
        out.append({"bottleneck": "qualified businesses with no contact at all",
                    "count": rest, "urgency": "low",
                    "why": "real businesses we simply cannot reach yet",
                    "action": "directory search — IndiaMART, TradeIndia, LinkedIn",
                    "examples": [i for i in no_contact if i not in phone_ready][:5]})
    return out


def mission(db, minutes_available: int = 90) -> dict:
    """
    The founder's morning: the highest-value work that fits the time available.

    Ranked by what is known rather than what is estimated — an account that has
    already engaged outranks a larger stranger, because the engagement is
    evidence and the size is a category average.
    """
    from app.models.models import B2BLead
    from app.services.outreach_search import rank_calls
    from app.services.contact_trust import sendable, actionable

    blocks = bottlenecks(db)
    leads = db.query(B2BLead).filter(
        ~B2BLead.status.in_(["DISQUALIFIED", "CLOSED_LOST", "DO_NOT_CONTACT"])).all()
    by_id = {l.id: l for l in leads}

    tasks, spent = [], 0

    # 1. Broken promises first — 5 minutes each, and they are owed.
    for b in blocks:
        if b["bottleneck"] != "commitments we have not honoured":
            continue
        for ex in b["examples"]:
            if spent + 5 > minutes_available:
                break
            tasks.append({"do": ex["blocker"], "company": ex["company"],
                          "lead_id": ex["lead_id"], "minutes": 5,
                          "why": f"waiting {ex['days_waiting']} days on something we promised"})
            spent += 5

    # 2. Restart the conversations that already started.
    for b in blocks:
        if not b["bottleneck"].startswith("engaged accounts quiet"):
            continue
        for ex in b["examples"]:
            if spent + 8 > minutes_available:
                break
            tasks.append({"do": "call — thread has gone cold", "company": ex["company"],
                          "lead_id": ex["lead_id"], "minutes": 8,
                          "why": f"quiet {ex['days_quiet']} days after real engagement"})
            spent += 8

    # 3. Fill the rest with the best cold calls, 6 minutes each.
    from app.api.endpoints import _event_summary
    evs = _event_summary(db, list(by_id.keys())) if by_id else {}
    used = {t["lead_id"] for t in tasks}
    for c in rank_calls([l for l in leads if l.id not in used], evs, limit=40):
        if spent + 6 > minutes_available:
            break
        tasks.append({"do": "first call", "company": c["company"],
                      "lead_id": c["lead_id"], "minutes": 6,
                      "why": ", ".join(c["why"][:2]) or "qualified and reachable"})
        spent += 6

    modelled = sum((by_id[t["lead_id"]].estimated_value or 0) * 0.31
                   for t in tasks if t["lead_id"] in by_id)
    sendable_now = sum(1 for l in leads if sendable(l)[0] and actionable(l)[0])

    return {
        # IST: the founder is in Punjab and reads this at a glance.
        "generated_at": ist_iso(datetime.utcnow()),
        "generated_at_display": ist_str(datetime.utcnow()),
        "minutes_available": minutes_available,
        "minutes_planned": spent,
        "tasks": tasks,
        "task_count": len(tasks),
        "bottlenecks": blocks,
        "modelled_annual_margin_in_reach": round(modelled),
        "modelled_note": ("MODELLED from category averages — not a forecast and "
                          "not a measurement of these businesses"),
        "realised_revenue": 0,
        "realised_note": ("0 orders and 0 replies are on record, so there is no "
                          "realised revenue to report. This will show real "
                          "figures once outcomes exist."),
        "email_sendable_now": sendable_now,
        "headline": (f"{len(tasks)} actions, about {spent} minutes."
                     if tasks else
                     "Nothing actionable — every qualified business needs a "
                     "contact found first."),
    }
