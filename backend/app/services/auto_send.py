"""
Automatic outreach approval (29 Oct 2026, Hiten 3:21 PM IST, relayed by architect):
"Fully automatic: send emails and WhatsApp without my approval, within the limits".

This does NOT impersonate the founder. It is a separate, explicit, env-gated
path that marks a draft AUTO_APPROVED (approved_by=APPROVED_BY) and records an
OUTREACH_APPROVED event with actor AUTO_ACTOR -- only after every gate below
passes. send_queue counts AUTO_ACTOR approvals and AUTO_APPROVED drafts only
while OUTREACH_AUTO_SEND=1, and re-runs these gates at send time, so the
rollback is: set OUTREACH_AUTO_SEND=0 (restart purity-worker/purity-api).

  OUTREACH_AUTO_SEND        0   master switch (default OFF)
  AUTO_SEND_HOLD_LEAD_IDS       extra comma list held out of automatic sends
  WA_AUTO_TEMPLATES         pb_b2b_fullrange_hinglish  Meta-approved template(s)

Gates (all fail closed):
  content  : wording_gate (samples, 100%, freeze-dried Bold/Ultra, Ultra no-chicory)
             + any 'sample'/'tasting' + Ultra Blend / Variety Box never 'pure' or
             'no chicory' + Ultra Blend must be described with chicory
             + no 'small/paid first order', no 'no MOQ'
             + warm_followup extra rules (no trial orders, no 'replace supplier')
             + any '[[' marker -> HOLD for Hiten (never sent)
  lead     : email on file, recipient matches, not do_not_call, not opted out,
             not supplier, trust may_send, bounce/suppression guard, phone not on
             block list / DND / hard block, 7-day per-domain rule, hold lists,
             lead 1245 always held, decision engine says SEND
  volume   : only 09:30-19:00 IST Mon-Sat; never more outstanding approvals than
             EMAIL_DAILY_CAP minus today's sends (pacing itself stays in
             human_pace / email_sender / send_queue.drain)
Statuses BLOCKED_* (incl. the 385 BLOCKED_AUTO_APPROVAL / BLOCKED_NOT_SENDABLE
and BLOCKED_LOW_TRUST) are never read: only DRAFT / PENDING / EDITED are candidates.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
AUTO_ACTOR = "AUTO_SEND"
APPROVED_BY = "auto (Hiten 29 Oct 3:21 PM)"
AUTO_STATUS = "AUTO_APPROVED"
CANDIDATE_STATUSES = ("DRAFT", "PENDING", "EDITED")
LIVE_APPROVED = ("FOUNDER_APPROVED", "APPROVED", "QUEUED", "SENDING", AUTO_STATUS)
ALWAYS_HELD = {1245}          # do_not_call, blocked (Hiten)
START, END = dtime(9, 30), dtime(19, 0)


def _on(name, default="0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _on("OUTREACH_AUTO_SEND", "0")


def hold_ids() -> set:
    out = set(ALWAYS_HELD)
    for var in ("AUTO_SEND_HOLD_LEAD_IDS", "EMAIL_HOLD_LEAD_IDS"):
        for x in (os.getenv(var, "") or "").split(","):
            x = x.strip()
            if x.isdigit():
                out.add(int(x))
    return out


def in_window(now=None) -> bool:
    n = (now or datetime.now(IST)).astimezone(IST)
    return n.weekday() <= 5 and START <= n.time() < END


# ------------------------------------------------------------------ content
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n")
_ULTRA = re.compile(r"\bultra\s*blend\b", re.I)
_VARIETY = re.compile(r"\bvariety\s*(box|pack)\b", re.I)
_PURE = re.compile(r"\bpure\b|\bno\s+chicory\b|\bchicory[\s-]*free\b|\bwithout\s+chicory\b|\bzero\s+chicory\b", re.I)
_CHICORY = re.compile(r"chicory", re.I)
_BANNED = [
    (re.compile(r"\bsamples?\b", re.I), "mentions samples (never offered)"),
    (re.compile(r"\btasting\b", re.I), "mentions tasting (never offered)"),
    (re.compile(r"100\s*(%|percent|per\s*cent)", re.I), "says 100% (say 'pure coffee')"),
    (re.compile(r"\b(small\s+)?(paid\s+)?first\s+order\b", re.I), "'first order' line is not used"),
    (re.compile(r"\bno\s+moq\b|\bno\s+minimum\s+order", re.I), "'no MOQ' line is not used"),
]


def content_check(subject: str, body: str) -> tuple[str, str]:
    """('OK'|'HOLD'|'BLOCK', why). HOLD = needs Hiten (markers); BLOCK = bad copy."""
    text = f"{subject or ''}\n{body or ''}"
    if "[[" in text or "]]" in text:
        return "HOLD", "contains a [[ ]] marker for Hiten to fill"
    try:
        from app.services import wording_gate
        ok, why = wording_gate.check(subject or "", body or "")
    except Exception as exc:  # noqa: BLE001
        return "BLOCK", f"wording gate unavailable ({exc.__class__.__name__})"
    if not ok:
        return "BLOCK", why
    for rx, why in _BANNED:
        if rx.search(text):
            return "BLOCK", why
    try:
        from app.services.warm_followup import _EXTRA_BANNED
        for rx, why in _EXTRA_BANNED:
            if rx.search(text):
                return "BLOCK", why
    except Exception:  # noqa: BLE001
        return "BLOCK", "warm wording rules unavailable"
    ultra_ok = False
    for s in (x.strip() for x in _SPLIT.split(text)):
        if not s:
            continue
        if _ULTRA.search(s):
            if _PURE.search(s):
                return "BLOCK", "Ultra Blend called pure / no chicory (it contains chicory)"
            if _CHICORY.search(s):
                ultra_ok = True
        if _VARIETY.search(s) and _PURE.search(s):
            return "BLOCK", "Variety Box called pure / no chicory (it includes Ultra Blend)"
    if _ULTRA.search(text) and not ultra_ok:
        return "BLOCK", "Ultra Blend mentioned without saying it contains chicory"
    return "OK", ""


# ------------------------------------------------------------------ lead
def lead_check(db, lead, recipient: str = "", decision: bool = True) -> tuple[bool, str]:
    if lead is None:
        return False, "lead not found"
    if lead.id in hold_ids():
        return False, "on the hold list"
    email = (getattr(lead, "email", "") or "").strip().lower()
    if not email:
        return False, "no email on file"
    if recipient and recipient.strip().lower() != email:
        return False, "draft recipient differs from the lead's current email"
    if bool(getattr(lead, "do_not_call", False)):
        return False, "do_not_call"
    for attr, bad in (("contact_status", {"OPTED_OUT", "DO_NOT_CONTACT"}),
                      ("status", {"OPT_OUT", "DO_NOT_CONTACT", "UNSUBSCRIBED"}),
                      ("consent_status", {"OPT_OUT", "REVOKED"})):
        if (getattr(lead, attr, "") or "").upper() in bad:
            return False, f"{attr}={(getattr(lead, attr, '') or '').upper()}"
    # Channel-specific authorization is mandatory: general CRM trust or
    # WhatsApp/call consent must never authorize email to this address.
    try:
        from types import SimpleNamespace
        from app.services.outbound_cohort import email_refusal
        refusal = email_refusal(SimpleNamespace(lead_id=lead.id, to_email=email), db)
        if refusal:
            return False, f"email channel authorization: {refusal}"
    except Exception as exc:  # fail closed if evidence lookup is unavailable
        return False, f"email authorization gate unavailable ({exc.__class__.__name__})"
    try:
        from app.services.supplier_guard import is_supplier
        sup, why = is_supplier(lead)
        if sup:
            return False, f"supplier ({why})"
        from app.services.trust_promoter import may_send
        ok, why = may_send(lead)
        if not ok:
            return False, f"trust: {why}"
        from app.services.bounce_guard import check as bounce
        ok, why = bounce(email, db)
        if not ok:
            return False, f"bounce/suppression: {why}"
        from app.services.whatsapp_pacer import number_suppressed
        for num in {getattr(lead, a, "") for a in ("phone", "whatsapp_number", "consent_phone")}:
            if num:
                why = number_suppressed(num)
                if why in ("hard-blocked", "on the block list / DND"):
                    return False, f"phone {why}"
        from app.services import domain_rule
        ok, why = domain_rule.check(db, email)
        if not ok:
            return False, why
        if decision:
            from app.services.decision_engine import evaluate_next_action
            d = evaluate_next_action(lead, db)
            if d.get("action") != "SEND":
                return False, f"decision engine: {d.get('action')} ({str(d.get('reason'))[:120]})"
    except Exception as exc:  # noqa: BLE001  fail closed
        return False, f"gate unavailable ({exc.__class__.__name__})"
    return True, "ok"


# ------------------------------------------------------------------ email
def outstanding(db) -> int:
    """Auto approvals not yet sent or revoked."""
    from app.models.models import EmailDraft
    return db.query(EmailDraft).filter(EmailDraft.status == AUTO_STATUS).count()


def room(db) -> int:
    from app.services import human_pace
    if not human_pace.email_sending_enabled()[0]:
        return 0  # 3 Nov: EMAIL_SENDING_ENABLED off -> approve nothing
    cap = human_pace.effective_daily_cap()  # 4 Nov: hard max 15, DB-backed count
    return max(0, cap - human_pace.daily_count() - outstanding(db))


def evaluate_email(db, dry_run: bool = True, now=None, limit: int | None = None) -> dict:
    """Walk open drafts; approve (or, in dry_run, report) the ones that pass."""
    from app.models.models import B2BLead, EmailDraft
    rep = {"window_open": in_window(now), "room": room(db), "approved": [], "would_approve": [],
           "held_markers": [], "blocked_content": [], "blocked_lead": [], "skipped_dup_lead": 0}
    drafts = (db.query(EmailDraft).filter(EmailDraft.status.in_(CANDIDATE_STATUSES))
              .order_by(EmailDraft.created_at.asc(), EmailDraft.id.asc()).all())
    seen = set()
    budget = rep["room"] if limit is None else min(rep["room"], limit)
    for d in drafts:
        verdict, why = content_check(d.subject, d.body)
        if verdict == "HOLD":
            rep["held_markers"].append(d.id); continue
        if verdict == "BLOCK":
            rep["blocked_content"].append((d.id, why)); continue
        if d.lead_id in seen:
            rep["skipped_dup_lead"] += 1; continue
        if db.query(EmailDraft.id).filter(EmailDraft.lead_id == d.lead_id,
                                          EmailDraft.status.in_(LIVE_APPROVED)).first():
            rep["skipped_dup_lead"] += 1; continue
        lead = db.query(B2BLead).filter(B2BLead.id == d.lead_id).first()
        ok, why = lead_check(db, lead, d.recipient or "")
        if not ok:
            rep["blocked_lead"].append((d.id, why)); continue
        seen.add(d.lead_id)
        rep["would_approve"].append(d.id)
        if dry_run or not enabled() or not rep["window_open"] or len(rep["approved"]) >= budget:
            continue
        approve_email(db, lead, d)
        rep["approved"].append(d.id)
    if not dry_run and rep["approved"]:
        db.commit()
    return rep


def approve_email(db, lead, draft) -> None:
    from app.models.models import WorkflowEvent
    now = datetime.utcnow()
    draft.status = AUTO_STATUS
    draft.approved_at = now
    draft.reason = ((draft.reason or "") + f" | approved_by: {APPROVED_BY}")[:1000]
    db.add(WorkflowEvent(lead_id=lead.id, event_type="OUTREACH_APPROVED", actor=AUTO_ACTOR,
                         channel="approval",
                         payload={"to": lead.email, "touch": draft.follow_up_type or "",
                                  "draft_id": draft.id, "approved_by": APPROVED_BY,
                                  "approved_at": now.isoformat()},
                         occurred_at=now))


def send_time_check(db, lead, draft) -> tuple[bool, str]:
    """Re-run every gate immediately before an AUTO_APPROVED draft is sent."""
    if not enabled():
        return False, "OUTREACH_AUTO_SEND is off"
    verdict, why = content_check(draft.subject, draft.body)
    if verdict != "OK":
        return False, f"content {verdict}: {why}"
    return lead_check(db, lead, draft.recipient or "", decision=False)


# ------------------------------------------------------------------ whatsapp
def wa_templates() -> set:
    return {t.strip() for t in (os.getenv("WA_AUTO_TEMPLATES", "pb_b2b_fullrange_hinglish") or "").split(",")
            if t.strip()}


def evaluate_whatsapp(db, dry_run: bool = True) -> dict:
    """Auto-approve PENDING WhatsApp drafts that are the FIRST message to a
    call-consented lead and use a Meta-approved template. Nudges stay with Hiten.
    The pacer tick (cron) still sends at most one per tick with 3-12 min gaps."""
    from app.models.models import B2BLead
    from app.services import whatsapp_drafts, whatsapp_pacer
    rep = {"approved": [], "would_approve": [], "blocked": []}
    default_tpl = sorted(wa_templates())[0] if wa_templates() else ""
    for did, v in whatsapp_drafts.all_drafts().items():
        if v.get("status") != "PENDING":
            continue
        lead = db.query(B2BLead).filter(B2BLead.id == v.get("lead_id")).first()
        params = v.get("params") or {}
        ftype = params.get("followup_type") or "first"
        if lead is None:
            rep["blocked"].append((did, "lead not found")); continue
        if lead.id in hold_ids():
            rep["blocked"].append((did, "on the hold list")); continue
        if ftype not in ("first", "warm_followup"):
            rep["blocked"].append((did, f"{ftype}: nudges stay with Hiten")); continue
        if params.get("flags"):
            rep["blocked"].append((did, "has founder-fill flags")); continue
        tpl = v.get("template_key") or ""
        if tpl not in wa_templates():
            if ftype == "warm_followup" and default_tpl:
                tpl = default_tpl          # the first warm message goes out as the approved full-range template
            else:
                rep["blocked"].append((did, f"template {tpl!r} is not Meta-approved")); continue
        ok, why = whatsapp_pacer.eligible(db, lead)
        if not ok:
            rep["blocked"].append((did, why)); continue
        rep["would_approve"].append(did)
        if dry_run or not enabled():
            continue
        whatsapp_drafts.auto_approve(did, APPROVED_BY, tpl)
        rep["approved"].append(did)
    return rep
