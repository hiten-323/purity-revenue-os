"""Catalogue / info-request follow-up (13 Oct 2026, founder-approved).

On a CATALOGUE_REQUESTED call outcome (info-by-email maps here too):
  1. an email the caller spoke on the call is written to lead.email, marked
     email_trust="UNVERIFIED" -- only when the lead has no email or an
     unverified one (a VERIFIED/TRUSTED/VALIDATED email is never overwritten);
  2. a catalogue EmailDraft is created with status DRAFT -> it waits in the
     founder approval queue; nothing here ever sends;
  3. an OPEN OutreachReminder is created; materialize_due_followups() turns it
     into a DRAFT follow-up email 2 days after the catalogue email was SENT if
     no reply arrived (the reply handler deletes OPEN reminders).
Kill switch: CATALOGUE_FOLLOWUP_ENABLED=0. Leads in CATALOGUE_FOLLOWUP_SKIP_LEADS
(comma ids) get the email write-back but no drafts.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

from app.services.identity import EMAIL_RE  # single source
STRONG_TRUST = {"VERIFIED", "TRUSTED", "VALIDATED"}
TYPE_CATALOGUE = "catalogue"
TYPE_FOLLOWUP = "catalogue_followup"
REMINDER_STEP = 901
FOLLOWUP_DAYS = 2


def enabled() -> bool:
    return (os.getenv("CATALOGUE_FOLLOWUP_ENABLED", "1") or "1").strip() not in ("0", "false", "off", "no")


def _skip_ids() -> set[int]:
    raw = os.getenv("CATALOGUE_FOLLOWUP_SKIP_LEADS", "1592")
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


def extract_email(*texts) -> str | None:
    for t in texts:
        if not t:
            continue
        m = EMAIL_RE.search(str(t))
        if m:
            return m.group(0).strip(".").lower()
    return None


def write_back_email(lead, email: str | None) -> bool:
    if not email:
        return False
    cur = (lead.email or "").strip().lower()
    trust = (getattr(lead, "email_trust", None) or "").upper()
    if cur == email:
        return False
    if cur and trust in STRONG_TRUST:
        return False  # never overwrite a verified email
    lead.email = email
    lead.email_trust = "UNVERIFIED"
    lead.email_source = "CALL_SPEECH"
    lead.email_approved_by_founder = False
    return True


def _catalogue_line() -> str:
    try:
        from app.services.email_sender import catalogue_url
        url = catalogue_url()
    except Exception:  # noqa: BLE001
        url = ""
    return (f"You can view our catalogue here: {url}" if url
            else "Please find our catalogue attached.")


def _body(company: str) -> str:
    return (f"Namaste,\n\nThank you for speaking with us about Purity Beans. As requested, "
            f"here are the details for {company}.\n\n"
            "Purity Beans is India's Cleanest Instant Coffee, packed in glass jars.\n\n"
            f"{_catalogue_line()}\n\n"
            "Our 4-blend Variety Box (Rs 877) or Gourmet Duo Mini (Rs 570) can be ordered directly here: "
            "https://www.p3online.in/products/variety-box\n\n"
            "Reply with your preferred quantity and we will share rate details, "
            "or tell us a good time to call.\n\nRegards,\nHiten Jain\nFounder, Purity Beans | p3online.in\nWhatsApp: +91 90849 58495\nBusiness phone: +91 90849 58495")


def on_catalogue_outcome(db, lead, *, summary=None, transcript=None, details=None) -> dict:
    if not enabled():
        return {"skipped": "disabled"}
    from app.models.models import EmailDraft, OutreachReminder
    d = details or {}
    email = extract_email(d.get("email") if isinstance(d, dict) else None, summary, transcript)
    wrote = write_back_email(lead, email)
    if lead.id in _skip_ids():
        return {"email_written": wrote, "skipped": "skip_list"}
    to = (lead.email or "").strip()
    if not to:
        return {"email_written": wrote, "skipped": "no_email"}
    open_q = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id,
                                          EmailDraft.follow_up_type == TYPE_CATALOGUE,
                                          EmailDraft.status.in_(["DRAFT", "PENDING", "EDITED", "FOUNDER_APPROVED", "QUEUED"]))
    if open_q.first():
        return {"email_written": wrote, "skipped": "draft_exists"}
    now = datetime.utcnow()
    company = lead.company or "your business"
    note = "" if _catalogue_line().startswith("You can view") else " ATTACH catalogue PDF before sending."
    draft = EmailDraft(lead_id=lead.id, follow_up_type=TYPE_CATALOGUE, recipient=to,
                       subject=f"Purity Beans catalogue - instant coffee for {company}",
                       body=_body(company), status="DRAFT", campaign="catalogue_request",
                       reason=("Asked for catalogue/info on call. Email from call transcript is UNVERIFIED"
                               " - confirm before sending." + note),
                       created_at=now, draft_created_at=now)
    db.add(draft)
    db.add(OutreachReminder(lead_id=lead.id, channel="email", sequence_step=REMINDER_STEP,
                            due_at=now + timedelta(days=FOLLOWUP_DAYS), status="OPEN",
                            reason="Catalogue follow-up: draft a nudge 2 days after catalogue email is SENT if no reply",
                            created_at=now))
    log.info("catalogue draft queued for approval lead=%s", lead.id)
    return {"email_written": wrote, "draft": "DRAFT"}


def materialize_due_followups(db, now: datetime | None = None) -> int:
    """OPEN catalogue reminders -> DRAFT follow-up email (approval queue only)."""
    if not enabled():
        return 0
    from app.models.models import EmailDraft, OutreachReminder
    now = now or datetime.utcnow()
    made = 0
    for rem in db.query(OutreachReminder).filter(OutreachReminder.sequence_step == REMINDER_STEP,
                                                 OutreachReminder.status == "OPEN").all():
        sent = (db.query(EmailDraft).filter(EmailDraft.lead_id == rem.lead_id,
                                             EmailDraft.follow_up_type == TYPE_CATALOGUE,
                                             EmailDraft.status == "SENT")
                .order_by(EmailDraft.sent_at.desc()).first())
        if not sent or not sent.sent_at or now < sent.sent_at + timedelta(days=FOLLOWUP_DAYS):
            continue
        if (sent.reply_status or "").upper() in ("REPLIED", "YES", "TRUE") or sent.replied_at:
            rem.status, rem.done_at = "DONE", now
            continue
        db.add(EmailDraft(lead_id=rem.lead_id, follow_up_type=TYPE_FOLLOWUP, recipient=sent.recipient,
                          subject="Re: " + (sent.subject or "Purity Beans catalogue"),
                          body=("Namaste,\n\nJust following up on the Purity Beans catalogue we sent. "
                                "Would you like us to share rate details?\n\n"
                                "Regards,\nHiten Jain\nFounder, Purity Beans | p3online.in\nWhatsApp: +91 90849 58495\nBusiness phone: +91 90849 58495"),
                          status="DRAFT", campaign="catalogue_request",
                          reason="2-day no-reply follow-up to catalogue email", created_at=now,
                          draft_created_at=now))
        rem.status, rem.done_at = "DONE", now
        made += 1
    return made
