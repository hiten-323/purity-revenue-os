"""
Account 360 — the workspace an engaged opportunity gets instead of a draft.

WHY THIS EXISTS
Up to first reply, the useful question is "what do I send?" After it, that is
the wrong question. What the founder needs is: what does this account actually
look like, what is holding it up, and what single thing moves it.

So every field here traces to a stored interaction or a real event. Anything not
learned yet reads "not recorded" rather than being inferred from company size or
category — a guessed monthly consumption is worse than a blank one, because the
founder will quote against it.

THE PART THAT IS ACTUALLY NEW
diagnose() answers "why hasn't this converted?" from the record. Not a score, a
named blocker with the evidence behind it and the one action that clears it. A
reminder tells you to follow up; this tells you what to fix.
"""
from __future__ import annotations

from datetime import datetime

from app.services.timeutil import ist_iso, ist_str


def _latest(interactions, field):
    """Most recent non-empty value for a field, with when and how we learned it."""
    for i in sorted(interactions, key=lambda x: x.occurred_at or datetime.min,
                    reverse=True):
        v = getattr(i, field, None)
        if v not in (None, ""):
            return {"value": v,
                    "source": (i.method or "contact").replace("_", " "),
                    "at": ist_iso(i.occurred_at),
                    "at_display": ist_str(i.occurred_at)}
    return None


# Objections worth naming, matched against what the founder actually wrote down.
_OBJECTION_PATTERNS = (
    ("price", ("price", "costly", "expensive", "rate", "cheaper", "margin")),
    ("existing supplier", ("already", "current supplier", "nescafe", "bru",
                           "existing", "tie-up", "contract")),
    ("no requirement", ("no need", "not required", "don't need", "no requirement")),
    ("timing", ("later", "next month", "after", "season", "busy", "festival")),
    ("product fit", ("quality", "taste", "sample", "strength", "pack size")),
)


def _objections(interactions) -> list[dict]:
    out = []
    for i in interactions:
        blob = f"{i.remark or ''} {i.outcome or ''}".lower()
        if not blob.strip():
            continue
        for name, words in _OBJECTION_PATTERNS:
            if any(w in blob for w in words):
                out.append({"objection": name,
                            "said": (i.remark or i.outcome or "")[:160],
                            "at": i.occurred_at.isoformat() if i.occurred_at else None,
                            "via": (i.method or "").replace("_", " ")})
                break
    return out


def diagnose(lead, interactions, ev: dict) -> dict:
    """
    Why has this account not converted, and what clears it?

    Ordered by what actually blocks progress. The first matching cause wins,
    because handing the founder five simultaneous "blockers" is the same as
    handing them none.
    """
    now = datetime.utcnow()
    last = max((i.occurred_at for i in interactions if i.occurred_at),
               default=None)
    days_quiet = (now - last).days if last else None
    dm = _latest(interactions, "decision_maker") or (
        {"value": lead.decision_maker, "source": "lead record", "at": None}
        if getattr(lead, "decision_maker", None) else None)
    stage = (lead.status or "").upper()

    def r(blocker, why, action, urgency):
        return {"blocker": blocker, "evidence": why,
                "next_best_action": action, "urgency": urgency}

    if stage in ("CLOSED_LOST", "DO_NOT_CONTACT"):
        return r("closed", f"stage is {stage}", "none — do not contact", "none")
    if stage == "ORDER_WON":
        return r("none", "order won", "watch for the reorder window", "low")

    # A promise we made and have not kept outranks anything the buyer owes us.
    if any((i.outcome or "") == "SAMPLE_REQUESTED" for i in interactions) \
            and not ev.get("sample_sent"):
        return r("sample requested but not dispatched",
                 "the buyer asked for a sample and no dispatch is recorded",
                 "dispatch the sample and confirm it went", "high")
    if any((i.outcome or "") == "SEND_PRICING" for i in interactions) \
            and not ev.get("proposal_sent"):
        return r("pricing requested but not sent",
                 "the buyer asked for pricing and no proposal is recorded",
                 "send pricing against their volumes", "high")

    if not dm:
        return r("no decision maker identified",
                 "no interaction has recorded who decides",
                 "call and ask who handles packaged beverage buying", "high")

    if days_quiet is not None and days_quiet >= 10:
        return r("gone quiet",
                 f"{days_quiet} days since the last recorded contact",
                 f"call {dm['value']} — the thread has cooled", "high")

    supplier = _latest(interactions, "current_supplier")
    if supplier and stage not in ("SAMPLE_SENT", "PROPOSAL_SENT", "MEETING_BOOKED"):
        return r("incumbent supplier in place",
                 f"buys {supplier['value']} (from a {supplier['source']})",
                 "offer a side-by-side comparison, not a switch", "medium")

    if ev.get("replied") and stage in ("DISCOVERED", "COLD", "QUALIFIED"):
        return r("replied but never spoken to",
                 "a reply is on record with no founder call after it",
                 "call — a reply is the cheapest conversation you will get",
                 "high")

    if not interactions:
        return r("never contacted", "no interaction recorded",
                 "first contact on the channel that is actually reachable",
                 "medium")

    return r("in progress", f"last contact {days_quiet} day(s) ago",
             "continue the current sequence", "low")


def build(lead, interactions, ev: dict, timeline: list) -> dict:
    """The full account view. Every value is stored or explicitly not recorded."""
    live = [i for i in interactions if not getattr(i, "superseded_by_id", None)]
    last_i = max(live, key=lambda x: x.occurred_at or datetime.min) if live else None

    def fld(name):
        v = _latest(live, name)
        return v or {"value": None, "source": None, "at": None}

    consumption = _latest(live, "monthly_consumption_kg")
    diag = diagnose(lead, live, ev)

    return {
        "lead_id": lead.id,
        "company": lead.company,
        "city": lead.city,
        "category": lead.division,
        "stage": lead.status,

        # Who and what — each with how we know it.
        "decision_maker": fld("decision_maker"),
        "designation": fld("designation"),
        "current_supplier": fld("current_supplier"),
        "monthly_consumption_kg": consumption or {"value": None, "source": None},
        "budget_range": fld("budget_range"),
        "price_sensitivity": fld("price_sensitivity"),
        "preferred_contact_time": fld("preferred_contact_time"),
        "preferred_contact_method": fld("preferred_contact_method"),

        # Commercial — modelled, and labelled as such.
        "modelled_annual_value": lead.estimated_value or 0,
        "modelled_annual_margin": round((lead.estimated_value or 0) * 0.31),
        "value_basis": ("sized from Google reviews"
                        if lead.maps_reviews_count is not None
                        else "category average — no size signal for this business"),

        # Where it stands.
        "last_interaction": ({
            "at": ist_iso(last_i.occurred_at),
            "at_display": ist_str(last_i.occurred_at),
            "method": last_i.method, "outcome": last_i.outcome,
            "remark": (last_i.remark or "")[:240]} if last_i else None),
        "next_commitment": fld("next_followup_date"),
        "emails_sent": ev.get("emails_sent", 0),
        "replied": ev.get("replied", False),
        "sample_sent": ev.get("sample_sent", False),
        "proposal_sent": ev.get("proposal_sent", False),

        "objections": _objections(live),
        "competitors_named": sorted({
            o["said"] and (_latest(live, "current_supplier") or {}).get("value")
            for o in _objections(live)} - {None, False, ""}),

        # The point of the screen.
        "diagnosis": diag,

        "interactions_recorded": len(live),
        "timeline": timeline,
        "contactable": {
            "phone": bool((lead.phone or "").strip() or (lead.whatsapp_number or "").strip()),
            "email_sendable": bool((lead.email or "").strip() and lead.email_verified),
        },
    }
