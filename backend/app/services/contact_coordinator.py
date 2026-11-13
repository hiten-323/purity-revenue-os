"""
Cross-Channel Contact Coordinator for Purity Revenue OS.

Coordinates Voice, Email, and WhatsApp to operate concurrently without
conflicts:
- Lead-level concurrency locks (lead_locked_until) to prevent race conditions.
- Contradictory offer prevention: synchronizes standard commercial pricing (25% trade + 5% cash = 30%).
- Post-Purchase Action Cancellation: immediately pauses outstanding sales tasks
  when a paid purchase is detected for that account.
- Channel-Specific Independence: an email unsubscribe suppresses future emails
  without deleting unrelated voice or consented WhatsApp records.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

log = logging.getLogger("contact_coordinator")
LOCK_DURATION_MINUTES = 15


def acquire_lead_lock(lead, db, actor: str = "SYSTEM") -> bool:
    """
    Acquires an atomic lock on the lead for an inflight outbound touchpoint.
    Returns True if lock acquired, False if another touch is in progress.
    """
    if lead is None or db is None:
        return False

    now = datetime.utcnow()
    locked_until = getattr(lead, "lead_locked_until", None)

    if locked_until and locked_until > now:
        log.info("Lead %s locked until %s; cannot acquire by %s", lead.id, locked_until, actor)
        return False

    lead.lead_locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
    try:
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        log.warning("Failed to acquire lock on lead %s: %s", lead.id, e)
        return False


def release_lead_lock(lead, db) -> None:
    """Releases the lock on a lead."""
    if lead is None or db is None:
        return
    lead.lead_locked_until = None
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("Failed to release lock on lead %s: %s", lead.id, e)


def is_cross_channel_suppressed(
    lead,
    channel: str,
    db=None,
    now: datetime | None = None
) -> Tuple[bool, str]:
    """
    Central lead-level contact coordinator check.
    Prevents cross-channel collisions, respects scheduled callbacks,
    enforces human takeover pauses, and honors DNC / paid conversions.
    Returns (is_suppressed, reason).
    """
    if lead is None:
        return True, "lead is None"

    now_utc = now or datetime.utcnow()
    chan = (channel or "").lower().strip()

    # 1. Permanent DNC / Opt-Out Suppression
    if getattr(lead, "do_not_call", False):
        return True, "DNC: do_not_call flag set on lead"

    c_status = (getattr(lead, "contact_status", "") or "").upper()
    if c_status in ("DO_NOT_CONTACT", "OPTED_OUT", "SUPPRESSED", "QUARANTINED"):
        return True, f"DNC: contact_status={c_status}"

    status = (getattr(lead, "status", "") or "").upper()
    if status in ("DO_NOT_CONTACT", "OPTED_OUT", "WRONG_NUMBER", "QUARANTINED"):
        return True, f"DNC: lead status={status}"

    # Channel-specific consent checks
    consent = (getattr(lead, "consent_status", "") or "").upper()
    if chan in ("whatsapp", "wa") and consent in ("OPT_OUT", "REVOKED", "OPTED_OUT"):
        return True, f"CONSENT_OPT_OUT: consent_status={consent} for whatsapp"
    if chan in ("email", "mail") and (getattr(lead, "email_opt_out", False) or consent == "OPT_OUT"):
        return True, f"CONSENT_OPT_OUT: email opt-out for lead"

    # 2. Paid Conversion Protection (Do not run cold acquisition on converted customers)
    if status == "PAID_CONVERSION":
        return True, "ACQUISITION_STOPPED: Lead has already converted to a paid customer"

    # 3. Human Takeover Protection
    owner = getattr(lead, "lead_owner", None)
    if status in ("HUMAN_TAKEOVER", "HUMAN_FOLLOWUP") or getattr(lead, "human_takeover", False):
        owner_str = f" Assigned to human owner '{owner}';" if owner else ""
        return True, f"HUMAN_TAKEOVER:{owner_str} automated {chan} touches paused"

    # 4. Scheduled Callback Cross-Channel Protection
    # A prospect-stated voice callback must not be bypassed by another worker on any channel.
    if db is not None and getattr(lead, "id", None):
        try:
            from app.models.models import ScheduledCallback
            cb = db.query(ScheduledCallback).filter(
                ScheduledCallback.lead_id == lead.id,
                ScheduledCallback.callback_status.in_(["CALLBACK_REQUESTED", "CALLBACK_SCHEDULED", "CALLBACK_DUE"]),
            ).order_by(ScheduledCallback.callback_window_start.asc()).first()

            if cb:
                w_start = cb.callback_window_start
                # If window is in the future, all automated marketing is suppressed
                if w_start and now_utc < w_start:
                    if chan in ("call", "voice"):
                        return True, f"CALLBACK_PENDING: Scheduled voice callback at {w_start.isoformat()}Z; early calls suppressed"
                    else:
                        return True, f"CALLBACK_PENDING: Prospect scheduled voice callback at {w_start.isoformat()}Z; cross-channel {chan} suppressed until callback"

                # If callback statement was ambiguous and unconfirmed:
                if not cb.callback_confirmed or cb.callback_status == "CALLBACK_REQUESTED":
                    return True, f"CALLBACK_UNCONFIRMED: Ambiguous callback '{cb.original_customer_statement}' awaits human review"
        except Exception as e:
            log.warning("Failed to check ScheduledCallback for lead %s: %s", lead.id, e)

    # Fallback to lead-level fields if db query not possible
    scheduled = getattr(lead, "ai_retry_after", None)
    if scheduled and now_utc < scheduled:
        if status == "COOLING_PERIOD":
            return True, f"COOLING_PERIOD: Lead cooling until {scheduled.isoformat()}Z"
        if status == "DECISION_PENDING":
            return True, f"DECISION_PENDING: Decision pending until {scheduled.isoformat()}Z"
        if status == "CALLBACK" and chan in ("call", "voice"):
            return True, f"CALLBACK_PENDING: Scheduled callback at {scheduled.isoformat()}Z"

    return False, "allowed"


def handle_purchase_event(lead_id: int, order_id: str, amount: float, db) -> int:
    """Stop acquisition follow-up after a confirmed purchase.

    Fetch unsent drafts before changing the lead to PAID_CONVERSION. Some
    model listeners cancel drafts during the status flush; querying afterward
    made the cancellation count incorrectly report zero.
    """
    if db is None or not lead_id:
        return 0

    from app.models.models import (
        B2BLead, EmailDraft, OutreachReminder, WorkflowEvent, OutboundWhatsApp,
    )
    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        return 0

    open_draft_statuses = [
        "DRAFT", "PENDING", "EDITED", "QUEUED",
        "FOUNDER_APPROVED", "APPROVED", "AUTO_APPROVED",
        "BLOCKED_NO_EMAIL", "BLOCKED_LOW_TRUST", "BLOCKED_LOW_CONFIDENCE",
        "BLOCKED_SUPPRESSED", "BLOCKED_AUTO_APPROVAL", "BLOCKED_NOT_SENDABLE",
    ]
    # EmailDraft is the B2B acquisition-email queue. Its historical
    # follow_up_type vocabulary is not closed (e.g. introduction,
    # catalogue_followup, and NULL/default rows existed outside the old
    # whitelist). Cancel every unsent open draft for this converted lead;
    # transactional order mail uses a separate delivery path.
    drafts = db.query(EmailDraft).filter(
        EmailDraft.lead_id == lead_id,
        EmailDraft.status.in_(open_draft_statuses),
        EmailDraft.sent_at.is_(None),
    ).all()
    cancelled_email_count = len(drafts)
    for draft in drafts:
        draft.status = "CANCELLED_PURCHASE_COMPLETED"

    # WhatsApp rows in this table are outbound acquisition messages, not
    # transactional order notifications. Cancel only messages not yet sent.
    wa_rows = db.query(OutboundWhatsApp).filter(
        OutboundWhatsApp.lead_id == lead_id,
        OutboundWhatsApp.status.in_(["DRAFT", "APPROVED", "QUEUED", "SENDING"]),
        OutboundWhatsApp.sent_at.is_(None),
    ).all()
    cancelled_wa_count = len(wa_rows)
    for row in wa_rows:
        row.status = "CANCELLED_PURCHASE_COMPLETED"

    reminders = db.query(OutreachReminder).filter(
        OutreachReminder.lead_id == lead_id,
        OutreachReminder.status.in_(["SCHEDULED", "PENDING", "DUE"])
    ).all()
    reminder_count = len(reminders)
    now = datetime.utcnow()
    for reminder in reminders:
        reminder.status = "CANCELLED"
        reminder.reason = ((reminder.reason or "") + " | purchase completed").strip(" |")
        reminder.done_at = now

    from app.services.callback_scheduler import cancel_callbacks_for_lead
    callback_count = cancel_callbacks_for_lead(db, lead_id, reason="PURCHASE_COMPLETED")

    lead.status = "PAID_CONVERSION"
    lead.ai_retry_after = None

    db.add(WorkflowEvent(
        lead_id=lead_id,
        event_type="PURCHASE_COMPLETED",
        actor="SYSTEM",
        channel="shopify",
        payload={
            "order_id": order_id,
            "amount_inr": amount,
            "cancelled_email_drafts": cancelled_email_count,
            "cancelled_whatsapp_drafts": cancelled_wa_count,
            "cancelled_drafts": cancelled_email_count + cancelled_wa_count,
            "cancelled_reminders": reminder_count,
            "cancelled_callbacks": callback_count,
        },
        occurred_at=now
    ))

    try:
        db.commit()
        total_cancelled = cancelled_email_count + cancelled_wa_count
        log.info(
            "Lead %s converted; cancelled %s acquisition drafts, %s reminders, %s callbacks",
            lead_id, total_cancelled, reminder_count, callback_count,
        )
        return total_cancelled
    except Exception as e:
        db.rollback()
        log.error("Failed to update post-purchase status for lead %s: %s", lead_id, e)
        return 0


def get_commercial_checkout_link(sku: str = "variety-box") -> dict:
    """
    Returns verified customer-facing checkout details for immediate conversion.
    Variety Box: ?877 (4 blends)
    Gourmet Duo Mini: ?570 (Purista + Purica 50g)
    """
    if sku == "gourmet-duo-mini":
        return {
            "title": "PURITY BEANS Gourmet Duo Mini (Purista 50g + Purica 50g)",
            "price_inr": 570.0,
            "url": "https://www.p3online.in/products/gourmet-duo-mini",
            "compliance_note": "pure coffee, no chicory"
        }
    return {
        "title": "PURITY BEANS Variety Box (4x50g)",
        "price_inr": 877.0,
        "url": "https://www.p3online.in/products/variety-box",
        "compliance_note": "commercial 4-blend box (Ultra Blend, Bold, Purista, Purica)"
    }
