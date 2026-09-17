"""Last-mile email governance guard.

The SMTP sender is intentionally a single chokepoint. A prospect email without
a lead_id is not attributable to a CRM record, so it cannot prove trust,
opt-out state, or account-level suppression. It must never reach SMTP.
"""
from __future__ import annotations

from typing import Callable


def install_email_send_guard() -> None:
    from app.services import email_sender

    original: Callable = email_sender.send_email
    if getattr(original, "_lead_identity_guard", False):
        return

    def guarded_send_email(email):
        sender = (email_sender.SENDER_EMAIL or "").strip().lower()
        recipient = (getattr(email, "to_email", "") or "").strip().lower()
        if recipient != sender and not getattr(email, "lead_id", None):
            email.status = "failed"
            email.error = "BLOCKED: outbound prospect email requires lead_id"
            return email

        lead_id = getattr(email, "lead_id", None)
        if recipient != sender and lead_id:
            try:
                from app.database.database import SessionLocal
                from app.models.models import B2BLead
                db = SessionLocal()
                try:
                    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
                    if lead is None:
                        email.status = "failed"
                        email.error = "BLOCKED: lead_id does not resolve to a CRM lead"
                        return email
                    status = (getattr(lead, "contact_status", "") or "").upper()
                    if status in {"OPTED_OUT", "DO_NOT_CONTACT"}:
                        email.status = "failed"
                        email.error = f"BLOCKED: contact_status={status}"
                        return email
                finally:
                    db.close()
            except Exception:
                email.status = "failed"
                email.error = "HELD: email governance lookup unavailable; email not sent"
                return email

        return original(email)

    guarded_send_email._lead_identity_guard = True
    email_sender.send_email = guarded_send_email
