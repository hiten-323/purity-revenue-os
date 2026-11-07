"""
Warm follow-up after an AI qualification call (29 Oct 2026, Hiten 2:09 PM).

"This is for leads who gave consent on WhatsApp or invited a founder's call,
meaning the lead can consider a backup instant coffee brand."

Entry point: on_call_outcome(), called once at the end of
founder_call_pipeline.record_ai_outcome(). Never raises into the call path.

Rules pinned here (and in tests/test_warm_followup.py):
  * Classes: INTERESTED, MAYBE (soft / somewhat interested), NOT_NOW, NO_STOP.
  * Founder-call asks (CALLBACK_REQUESTED / MEETING_REQUESTED / HUMAN_HANDOFF,
    or a lead already at FOUNDER_CALL_REQUESTED): NO automatic message. A
    founder task (the existing FOUNDER_CALL work item) plus a founder alert.
  * INTERESTED / MAYBE (incl. WHATSAPP_OPT_IN): lead marked warm, cold
    sequence stopped, ONE personalised follow-up drafted from the transcript.
  * Every draft is created unapproved (EmailDraft DRAFT / WA draft PENDING).
    This module never approves, never calls send_queue.approve and never
    sends. Approval is the founder's alone.
  * Framing: "keep Purity Beans as your backup / second instant coffee brand",
    never "replace / switch your supplier".
  * Channel: WhatsApp only when whatsapp_pacer.eligible() (explicit consent
    from the call, not suppressed/declined); otherwise email.
  * Nudges at +3 and +7 days after the follow-up went out (max 2, each a
    draft awaiting approval), then park 30 days. NOT_NOW re-opens after 30
    days as a draft. NO_STOP is suppressed permanently for this flow.
  * Copy obeys wording_gate (no samples, no "100%", Bold/Ultra Blend rules)
    plus: no "paid first order", no test calls / trial orders, no
    "replace/switch supplier". A draft that fails is kept but BLOCKED_WORDING.
  * Margin lines only from the founder-approved CALL_CONSTRAINTS (21 Oct).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

INTERESTED, MAYBE, NOT_NOW, NO_STOP = "INTERESTED", "MAYBE", "NOT_NOW", "NO_STOP"

FOUNDER_ASKS = frozenset({"CALLBACK_REQUESTED", "MEETING_REQUESTED", "HUMAN_HANDOFF"})
POSITIVE_KEYS = frozenset({"WHATSAPP_OPT_IN", "INTERESTED", "SEND_INFO_EMAIL"})
NEGATIVE_KEYS = frozenset({"NOT_INTERESTED", "REJECTED"})

EV_STARTED = "WARM_FOLLOWUP_STARTED"
EV_DRAFTED = "WARM_FOLLOWUP_DRAFTED"
EV_NUDGE = "WARM_FOLLOWUP_NUDGE_DRAFTED"
EV_PARKED = "WARM_FOLLOWUP_PARKED"
EV_REOPENED = "WARM_FOLLOWUP_REOPENED"
EV_SUPPRESSED = "WARM_FOLLOWUP_SUPPRESSED"
EV_FOUNDER = "WARM_FOUNDER_TASK"
EV_SEQ_STOP = "SEQUENCE_STOPPED_WARM"

NUDGE_DAYS = (3, 7)
PARK_DAYS = 30
WA_TEMPLATE = "pb_b2b_call_followup"
FOLLOWUP_TYPES = ("warm_followup", "warm_nudge_1", "warm_nudge_2", "warm_reopen")
OPEN = ("DRAFT", "PENDING", "EDITED", "FOUNDER_APPROVED", "QUEUED")
PLACEHOLDER = "[[FOUNDER TO FILL: %s]]"
# Founder ask, 29 Oct 3:13 PM IST: "tell factory rates me available with high margin oppurtunity"
FACTORY_LINE = "We supply at factory rates, so there's a strong margin opportunity for you."

_NOT_NOW_RX = re.compile(
    r"\b(not\s+now|later|next\s+(week|month|season|quarter|year)|after\s+(diwali|"
    r"festival|season)|abhi\s+nahi|baad\s+m(e|ein|ai)|filhaal|call\s+(me\s+)?later|"
    r"stock\s+(is\s+)?full|already\s+stocked)\b", re.I)
_MAYBE_RX = re.compile(
    r"\b(maybe|might|not\s+sure|think\s+about|consider|dekh(te|enge|ta)|soch(te|enge|ke)|"
    r"shayad|backup|second\s+brand|another\s+brand|option|compare)\b", re.I)
_TOPICS = {
    "price": re.compile(r"\b(price|rate|cost|kitna|daam|bhav|price\s*list|rate\s*list)\b", re.I),
    "moq": re.compile(r"\b(moq|minimum\s+order|min(imum)?\s+qty|quantity|kitne\s+piece)\b", re.I),
    "delivery": re.compile(r"\b(deliver|dispatch|shipping|courier|lead\s*time|kab\s+tak)\b", re.I),
    "margin": re.compile(r"\b(margin|discount|scheme|profit|commission)\b", re.I),
    "chicory": re.compile(r"\b(chicory|pure|blend|ingredient)\b", re.I),
    "range": re.compile(r"\b(catalog(ue)?|range|products?|variants?|jar|pack\s*size|sizes?)\b", re.I),
}
_EXTRA_BANNED = [
    (re.compile(r"\bpaid\s+first\s+order\b", re.I), "'paid first order' is not offered"),
    (re.compile(r"\b(test|trial)\s+(call|order)s?\b", re.I), "no test calls / trial orders"),
    (re.compile(r"\b(replace|switch(ing)?\s+(from|away)|instead\s+of\s+your|drop\s+your)\b", re.I),
     "framing must be backup/second brand, never replacing their supplier"),
]
SIG = "Hiten Jain\nPurity Beans / Pure Pantry Provisions\nIndia\nWebsite: https://p3online.in"


def enabled() -> bool:
    return (os.getenv("WARM_FOLLOWUP_ENABLED", "1") or "1").strip() not in ("0", "false", "no")


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------- classify
def classify(key: str, *, interest: str = "", details: dict | None = None,
             transcript: str = "", summary: str = "") -> str | None:
    """Map one AI-call result to a warm-followup class, or None (not ours)."""
    key = (key or "").strip().upper()
    text = f"{summary or ''}\n{transcript or ''}"
    det = details or {}
    objection = str(det.get("objection") or "").upper()
    lvl = (interest or "").strip().upper()
    timing = objection == "TIMING" or bool(_NOT_NOW_RX.search(text))
    if key == "OPT_OUT":
        return NO_STOP
    if key in NEGATIVE_KEYS:
        return NOT_NOW if timing else NO_STOP
    if key in FOUNDER_ASKS:
        return INTERESTED
    if key in POSITIVE_KEYS:
        if lvl in ("HOT", "HIGH"):
            return INTERESTED
        if (lvl in ("COOL", "LOW", "MEDIUM", "MAYBE", "SOFT")
                or objection in ("EXISTING_SUPPLIER", "PRICE", "TIMING")
                or _MAYBE_RX.search(text)):
            return MAYBE
        return INTERESTED
    return None   # retryable, wrong number/person, OTHER: not evidence either way


# ---------------------------------------------------------------- helpers
def _events(db, lead, *types):
    from app.models.models import WorkflowEvent
    q = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id)
    if types:
        q = q.filter(WorkflowEvent.event_type.in_(types))
    return q.order_by(WorkflowEvent.occurred_at.asc(), WorkflowEvent.id.asc()).all()


def _event(db, lead, etype, payload, channel="system"):
    from app.models.models import WorkflowEvent
    db.add(WorkflowEvent(lead_id=lead.id, event_type=etype, actor="warm_followup",
                         channel=channel, payload=payload, occurred_at=_now()))
    db.flush()


def suppressed(db, lead) -> bool:
    """Permanent for this flow: a NO/STOP from a call, or do-not-contact."""
    return bool(getattr(lead, "do_not_call", False)) or bool(_events(db, lead, EV_SUPPRESSED))


def _replied_since(db, lead, since) -> bool:
    from app.services.trust_promoter import REPLY_EVENTS
    return any((e.occurred_at or since) >= since for e in _events(db, lead, *REPLY_EVENTS))


def mark_warm(lead, db, cls, key):
    lead.intent_tier = "Warm"
    _event(db, lead, EV_STARTED, {"class": cls, "outcome": key})


def stop_cold_sequence(lead, db, why):
    """Stop the cold cadence: an event sequence_engine.state() honours, and any
    open cold-touch draft is set aside (CANCELLED_WARM, kept, reversible)."""
    from app.models.models import EmailDraft
    if not _events(db, lead, EV_SEQ_STOP):
        _event(db, lead, EV_SEQ_STOP, {"why": why})
    n = 0
    for d in db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                         EmailDraft.status.in_(OPEN)).all():
        if (d.follow_up_type or "") in FOLLOWUP_TYPES:
            continue
        d.status = "CANCELLED_WARM"
        d.reason = ((d.reason or "") + f" | set aside: {why}")[:1000]
        n += 1
    return n


_DIV_DISTRIBUTOR = {"distributor", "wholesaler", "stockist", "super_stockist", "wholesaler_agglo"}
_DIV_RETAIL = {"supermarket", "retail_kirana", "modern_trade", "grocery_chain", "grocery", "kirana",
               "kirana_store", "retail", "retail_chain", "department_store", "general_store"}
_DIV_CORPORATE = {"corporate_office", "corporate_gifting", "facility_management",
                  "institutional_buyer", "corporate_pantry"}
_DIV_KNOWN_OTHER = {"restaurant", "hotel", "cafe", "caterer", "horeca", "hospital", "school",
                    "college", "university", "manufacturing", "hotel_canteen", "guest_house",
                    "catering_contractor", "event_catering"}
_RETAIL_NAME = re.compile(
    r"\b(supermarket|super market|supermart|mega mart|mini mart|mart|kirana|grocery|grocer|"
    r"departmental|department store|general store|provision store|stores?)\b", re.I)


def _segment(lead) -> str:
    """Which founder-approved margin line fits this lead (29 Oct 2026 fix).

    The `segment` column defaults to 'corporate' for every lead discovery could
    not type (602 leads: restaurants, caterers, modern-trade stores...), so it
    is NOT evidence of a corporate buyer. Order: the typed `division` (Google
    Maps types / verified), then `segment` only when it says something other
    than the 'corporate' default, then the company name. A corporate line is
    only used when the division itself is corporate; anything unclear gets
    "other", which carries no figure at all.
    """
    div = str(getattr(lead, "division", "") or "").strip().lower()
    seg = str(getattr(lead, "segment", "") or "").strip().lower()
    name = str(getattr(lead, "company", "") or "")
    if div in _DIV_DISTRIBUTOR:
        return "distributor"
    if div in _DIV_RETAIL:
        return "retail"
    if div in _DIV_CORPORATE:
        return "corporate"
    if div in _DIV_KNOWN_OTHER:
        return "other"      # typed as something else (e.g. a restaurant): no margin figure
    # division empty / unknown: fall back to segment, never to the 'corporate' default
    s = " ".join([seg] + [str(getattr(lead, a, "") or "").lower() for a in ("industry",)])
    if any(w in s for w in ("distribut", "wholesal", "stockist")):
        return "distributor"
    if any(w in s for w in ("supermarket", "retail", "kirana", "grocery", "modern_trade", "modern trade")):
        return "retail"
    if seg in _DIV_CORPORATE:
        return "corporate"
    if _RETAIL_NAME.search(name):
        return "retail"
    return "other"


def margin_line(lead) -> str:
    """Only the founder-approved lines (CALL_CONSTRAINTS, 21 Oct). Other
    segments get no figure."""
    seg = _segment(lead)
    if seg == "distributor":
        return "For distributors and wholesalers the margin is up to 30% (25% trade discount + 5% cash discount)."
    if seg == "retail":
        return "For retail the margin is 30% (25% trade discount + 5% cash discount)."
    if seg == "corporate":
        return "Corporate pricing: up to 30% off."
    return ""


def _catalogue() -> str:
    try:
        from app.services.email_sender import catalogue_url
        return catalogue_url()
    except Exception:
        return ""


def wording_ok(subject: str, body: str) -> tuple[bool, str]:
    from app.services import wording_gate
    ok, why = wording_gate.check(subject, body)
    if not ok:
        return ok, why
    for rx, why in _EXTRA_BANNED:
        if rx.search(f"{subject}\n{body}"):
            return False, why
    return True, ""


# ---------------------------------------------------------------- compose
def compose(lead, cls, *, transcript="", summary="", stage="followup") -> dict:
    """One personalised follow-up from what was said on the call."""
    name = (getattr(lead, "contact_name", "") or "").strip().split(" ")[0] or "there"
    company = (getattr(lead, "company", "") or "your business").strip()
    text = f"{summary or ''}\n{transcript or ''}"
    asked = [t for t, rx in _TOPICS.items() if rx.search(text)]
    flags = []
    cat = _catalogue()
    if not cat:
        cat = PLACEHOLDER % "catalogue link (PURITY_BEANS_CATALOGUE_URL not set)"
        flags.append("catalogue_link_missing")

    lines = []
    if stage == "followup":
        lines.append(f"Hi {name},\n\nThank you for taking our call about Purity Beans instant coffee for {company}.")
        if cls == MAYBE:
            lines.append("No need to change anything you have today. Many shops keep a second instant "
                         "coffee brand as a backup for when their usual one runs short, and Purity Beans "
                         "fits well in that slot.")
        else:
            lines.append("We would be glad to be your backup or second instant coffee brand, alongside "
                         "what you already stock.")
        lines.append(FACTORY_LINE)
        answers = []
        if "price" in asked:
            answers.append("Prices: I will share our current price list. "
                           + PLACEHOLDER % "attach or paste the current price list")
            flags.append("price_list_placeholder")
        if "margin" in asked or "price" in asked:
            m = margin_line(lead)
            if m:
                answers.append(m)
        if "moq" in asked:
            answers.append("Minimum order: " + PLACEHOLDER % "MOQ for this segment")
            flags.append("moq_placeholder")
        if "delivery" in asked:
            answers.append("We dispatch PAN-India across India. Delivery time: "
                           + PLACEHOLDER % "delivery time to this city")
            flags.append("delivery_placeholder")
        if "chicory" in asked:
            answers.append("Purica, Purista, Bold and Prima are pure coffee with no chicory. "
                           "Ultra Blend is 70% coffee and 30% chicory.")
        if answers:
            lines.append("On what you asked:\n- " + "\n- ".join(answers))
        lines.append(f"Our catalogue: {cat}")
        lines.append("Would a short 10-minute call this week suit you? Tell me a time that works.")
    elif stage in ("warm_nudge_1", "warm_nudge_2"):
        lines.append(f"Hi {name},\n\nJust checking whether you had a chance to look at the Purity Beans "
                     f"catalogue ({cat}).")
        lines.append("Happy to be the backup instant coffee brand for {0}, so you are covered if your "
                     "regular one runs short.".format(company))
        lines.append(FACTORY_LINE)
        lines.append("A short 10-minute call whenever it suits you?")
    else:  # warm_reopen
        lines.append(f"Hi {name},\n\nWe spoke about a month ago about Purity Beans instant coffee. "
                     "You mentioned it was not the right time then.")
        lines.append(f"If a backup or second instant coffee brand would help now, here is our catalogue: {cat}")
        lines.append(FACTORY_LINE)
        lines.append("Would a short call this week or next suit you?")
    body = "\n\n".join(lines) + "\n\n" + SIG
    subject = {"followup": f"Purity Beans for {company}: catalogue and your questions",
               "warm_nudge_1": f"Re: Purity Beans for {company}",
               "warm_nudge_2": f"Re: Purity Beans for {company}",
               "warm_reopen": f"Purity Beans for {company}: a backup coffee brand"}.get(stage, "Purity Beans")
    return {"subject": subject, "body": body, "asked": asked, "flags": flags}


# ---------------------------------------------------------------- draft
def channel_for(db, lead) -> tuple[str, str]:
    try:
        from app.services.whatsapp_pacer import eligible
        ok, why = eligible(db, lead)
    except Exception as exc:  # noqa: BLE001
        ok, why = False, f"pacer check failed: {exc}"
    return ("whatsapp" if ok else "email"), why


def create_draft(lead, db, cls, *, stage="followup", transcript="", summary="") -> dict:
    """One unapproved draft. Never approves, never sends."""
    from app.services.gate1_holds import nudge_blocked, provider_armed
    if not provider_armed("draft"):
        return {"channel": "none", "draft_id": None, "status": "NOT_APPROVABLE",
                "terminal": True, "approvable": False, "why": "outbound flags are off"}
    held = nudge_blocked(lead, db)
    if held:
        return {"channel": "none", "draft_id": None, "status": "NOT_APPROVABLE",
                "terminal": True, "approvable": False, "why": held}
    from app.services.gate1_holds import _flag_on
    if any(_flag_on(name) for name in (
            "AUTO_WARM_ENABLED", "SMART_OUTREACH_ENABLED", "AUTO_OUTREACH_ENABLED",
            "EMAIL_SENDING_ENABLED", "KLAVIYO_WA_ENABLED", "AISENSY_ENABLED")):
        from app.services.outbound_cohort import warm_refusal
        outside = warm_refusal(lead, db)
        if outside:
            return {"channel": "none", "draft_id": None, "status": "NOT_APPROVABLE",
                    "terminal": True, "approvable": False, "why": outside}
    from app.models.models import EmailDraft
    ftype = "warm_followup" if stage == "followup" else stage
    msg = compose(lead, cls, transcript=transcript, summary=summary, stage=stage)
    ok, why = wording_ok(msg["subject"], msg["body"])
    chan, chan_why = channel_for(db, lead)
    note = ("warm %s after AI call (%s) - awaiting founder approval; never auto-approved"
            % (ftype, cls))
    if msg["flags"]:
        note += " | FILL BEFORE APPROVING: " + ", ".join(msg["flags"])
    if chan == "whatsapp" and ok:
        from app.services import whatsapp_drafts
        did = whatsapp_drafts.create(lead.id, WA_TEMPLATE, {
            "name": (getattr(lead, "contact_name", "") or "").split(" ")[0],
            "company": getattr(lead, "company", "") or "",
            "followup_type": ftype, "class": cls,
            "review_text": msg["body"][:1500], "flags": msg["flags"]})
        res = {"channel": "whatsapp", "draft_id": did, "status": "PENDING", "flags": msg["flags"]}
    else:
        if not (getattr(lead, "email", "") or "").strip():
            res = {"channel": "none", "draft_id": None, "status": "NO_CHANNEL",
                   "why": f"no email and WhatsApp not eligible ({chan_why})"}
            _event(db, lead, EV_DRAFTED if stage == "followup" else EV_NUDGE,
                   {"class": cls, "stage": ftype, **res})
            return res
        existing = db.query(EmailDraft).filter(
            EmailDraft.lead_id == lead.id, EmailDraft.follow_up_type == ftype,
            EmailDraft.status.in_(OPEN + ("BLOCKED_WORDING",))).first()
        if existing:
            return {"channel": "email", "draft_id": existing.id, "status": existing.status, "dedup": True}
        row = EmailDraft(lead_id=lead.id, follow_up_type=ftype, subject=msg["subject"],
                         body=msg["body"], recipient=lead.email, campaign="warm_followup",
                         status="DRAFT" if ok else "BLOCKED_WORDING",
                         reason=(note if ok else f"{note} | wording gate: {why}")[:1000],
                         approved_at=None)
        db.add(row)
        db.flush()
        res = {"channel": "email", "draft_id": row.id, "status": row.status, "flags": msg["flags"]}
        if chan == "whatsapp" and not ok:
            res["why"] = f"wording gate: {why}"
    _event(db, lead, EV_DRAFTED if stage == "followup" else EV_NUDGE,
           {"class": cls, "stage": ftype, "asked": msg["asked"], **res})
    return res


# ---------------------------------------------------------------- founder
def founder_task(lead, db, key, *, summary="", callback_window=""):
    """No message to the lead. A founder work item + an alert to Hiten."""
    from app.services import founder_call_pipeline as p
    if p._open_founder_call(lead, db) is None:
        try:
            p.request_founder_call(lead, db, reason=p.FOUNDER_CALL_ASKS.get(key, "asked on the AI call"),
                                   requested_by="AI", note=summary or "")
        except Exception as exc:  # noqa: BLE001  (illegal stage move: still alert)
            log.warning("warm_followup founder task for lead %s: %s", lead.id, exc)
    slot = (callback_window or getattr(lead, "founder_callback_window", "") or "").strip()
    already = any((e.payload or {}).get("alert_sent") for e in _events(db, lead, EV_FOUNDER)
                  if (e.occurred_at or _now()) > _now() - timedelta(hours=24))
    alert = {"sent": False, "reason": "already alerted in last 24h" if already else "not attempted"}
    if not already and os.getenv("PYTEST_CURRENT_TEST") is None and \
            (os.getenv("WARM_FOUNDER_ALERT", "1") or "1").strip() != "0":
        try:
            # Same founder-only path as the system alerts (Hiten's own inbox,
            # fixed recipient, never a prospect), with its own wording.
            from app.services.founder_brief import send_founder_system_email
            alert = send_founder_system_email(
                f"[Purity] Founder call requested: {getattr(lead, 'company', '')}",
                f"Lead #{lead.id} {getattr(lead, 'company', '')} ({getattr(lead, 'city', '') or '-'}) "
                f"asked on the AI call to speak with you ({key}).\n"
                f"Requested slot: {slot or 'not given'}\nSummary: {(summary or '-')[:800]}\n\n"
                "It is in the founder call queue on the dashboard. No message was sent to the lead.\n"
                "-- Purity Revenue OS")
        except Exception as exc:  # noqa: BLE001
            alert = {"sent": False, "reason": str(exc)[:200]}
    _event(db, lead, EV_FOUNDER, {"outcome": key, "requested_slot": slot or None,
                                  "note": (summary or "")[:500],
                                  "alert_sent": bool(alert.get("sent")),
                                  "alert_reason": alert.get("reason")})
    return {"founder_task": True, "alert": alert}


# ---------------------------------------------------------------- entry
def on_call_outcome(lead, db, key, *, summary="", interest="", transcript="",
                    details=None, callback_window="") -> dict:
    if not enabled():
        return {"skipped": "disabled"}
    key = (key or "").strip().upper()
    from app.services import founder_call_pipeline as p
    cls = classify(key, interest=interest, details=details, transcript=transcript, summary=summary)
    if cls is None:
        return {"skipped": "not a warm-followup outcome", "outcome": key}
    if cls == NO_STOP:
        if not _events(db, lead, EV_SUPPRESSED):
            _event(db, lead, EV_SUPPRESSED, {"outcome": key, "permanent": True})
        stop_cold_sequence(lead, db, f"AI call {key}")
        from app.models.models import EmailDraft
        for d in db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                             EmailDraft.follow_up_type.in_(FOLLOWUP_TYPES),
                                             EmailDraft.status.in_(OPEN)).all():
            d.status = "CANCELLED_WARM"
        return {"class": cls, "suppressed": True}
    if suppressed(db, lead):
        return {"class": cls, "skipped": "suppressed / do-not-contact"}
    if cls == NOT_NOW:
        stop_cold_sequence(lead, db, f"AI call {key}: not now")
        _event(db, lead, EV_PARKED, {"why": "not_now", "outcome": key,
                                     "reopen_at": (_now() + timedelta(days=PARK_DAYS)).isoformat()})
        return {"class": cls, "parked_days": PARK_DAYS}
    mark_warm(lead, db, cls, key)
    stop_cold_sequence(lead, db, f"warm after AI call {key}")
    if key in FOUNDER_ASKS or p.stage_of(lead) == p.FOUNDER_CALL_REQUESTED:
        return {"class": cls, **founder_task(lead, db, key, summary=summary,
                                             callback_window=callback_window)}
    if _events(db, lead, EV_DRAFTED):
        return {"class": cls, "skipped": "follow-up already drafted"}
    return {"class": cls, **create_draft(lead, db, cls, transcript=transcript, summary=summary)}


# ---------------------------------------------------------------- tick
def _sent_at(db, lead, ftype):
    from app.models.models import EmailDraft
    d = (db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                     EmailDraft.follow_up_type == ftype,
                                     EmailDraft.status == "SENT")
         .order_by(EmailDraft.sent_at.desc()).first())
    if d and d.sent_at:
        return d.sent_at
    try:
        from app.services import whatsapp_drafts
        for v in whatsapp_drafts.all_drafts().values():
            if v.get("lead_id") == lead.id and v.get("status") == "SENT" and \
                    (v.get("params") or {}).get("followup_type") == ftype and v.get("sent_at"):
                return datetime.fromisoformat(str(v["sent_at"]).rstrip("Z"))
    except Exception:
        pass
    return None


def tick(db, now: datetime | None = None, dry_run: bool = False) -> dict:
    """Daily: nudge drafts at +3/+7 days, park after the 2nd, re-open after 30.
    Creates drafts only; approval stays with the founder."""
    from app.models.models import B2BLead
    now = now or _now()
    out = {"nudges": [], "parked": [], "reopened": []}
    if not enabled():
        return {**out, "skipped": "disabled"}
    from app.services.gate1_holds import nudge_blocked, provider_armed
    if not provider_armed("draft"):
        return {**out, "skipped": "outbound flags off"}
    ids = {e.lead_id for e in _events_all(db, EV_STARTED, EV_PARKED)}
    for lead in db.query(B2BLead).filter(B2BLead.id.in_(ids)).all():
        if nudge_blocked(lead, db) or suppressed(db, lead) or _open_warm(db, lead):
            continue
        parks = _events(db, lead, EV_PARKED)
        reopens = _events(db, lead, EV_REOPENED)
        if parks and len(reopens) < len(parks):
            last = parks[-1]
            reopen_at = (last.occurred_at or now) + timedelta(days=PARK_DAYS)
            if now >= reopen_at and not _replied_since(db, lead, last.occurred_at or now):
                out["reopened"].append(lead.id)
                if not dry_run:
                    _event(db, lead, EV_REOPENED, {"after_days": PARK_DAYS})
                    create_draft(lead, db, MAYBE, stage="warm_reopen")
            continue
        if reopens:
            continue   # one re-open per lead; after that the founder decides
        base = _sent_at(db, lead, "warm_followup")
        if base is None or _replied_since(db, lead, base):
            continue
        nudges = _events(db, lead, EV_NUDGE)
        n = len(nudges)
        if n < len(NUDGE_DAYS) and now >= base + timedelta(days=NUDGE_DAYS[n]):
            out["nudges"].append((lead.id, n + 1))
            if not dry_run:
                create_draft(lead, db, MAYBE, stage=f"warm_nudge_{n + 1}")
        elif n >= len(NUDGE_DAYS) and now >= base + timedelta(days=NUDGE_DAYS[-1] + 7):
            out["parked"].append(lead.id)
            if not dry_run:
                _event(db, lead, EV_PARKED, {"why": "no reply after 2 nudges",
                                             "reopen_at": (now + timedelta(days=PARK_DAYS)).isoformat()})
    if not dry_run:
        db.commit()
    return out


def _open_warm(db, lead) -> bool:
    from app.models.models import EmailDraft
    if db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                   EmailDraft.follow_up_type.in_(FOLLOWUP_TYPES),
                                   EmailDraft.status.in_(OPEN + ("BLOCKED_WORDING",))).first():
        return True
    try:
        from app.services import whatsapp_drafts
        return any(v.get("lead_id") == lead.id and v.get("status") in ("PENDING", "APPROVED")
                   for v in whatsapp_drafts.all_drafts().values())
    except Exception:
        return False


def _events_all(db, *types):
    from app.models.models import WorkflowEvent
    return db.query(WorkflowEvent).filter(WorkflowEvent.event_type.in_(types)).all()
