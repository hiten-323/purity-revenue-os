"""
Founder actions — the only human decisions this system requires.

Everything else is automated: discovery, enrichment, sequence cadence, draft
generation, pacing, reply sync, and (after approval) delivery. The founder
opens the approval queue, says yes or no, and the worker does the rest.

WHY A DEDICATED TABLE
WorkflowEvent already records OUTREACH_APPROVED. That is the operational signal
the send drain reads. founder_actions is the human-readable audit of the same
decision: who approved what, against which draft, for which contact, with what
note. Querying "what has the founder decided?" should not require reconstructing
an event log — it should be a table named for that question.

RULE
SYSTEM never writes APPROVE_* rows. Only FOUNDER (or an explicit human actor)
may. A process that stamps founder consent on its own is the failure mode this
table exists to prevent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship

from app.database.database import Base


# Action types the founder is allowed to take. Everything else is system work.
APPROVE_OUTREACH = "APPROVE_OUTREACH"
REJECT_OUTREACH = "REJECT_OUTREACH"
REVOKE_APPROVAL = "REVOKE_APPROVAL"
APPROVE_CAMPAIGN = "APPROVE_CAMPAIGN"
REJECT_CAMPAIGN = "REJECT_CAMPAIGN"
APPROVE_SCRIPT = "APPROVE_SCRIPT"

FOUNDER_ACTION_TYPES = (
    APPROVE_OUTREACH, REJECT_OUTREACH, REVOKE_APPROVAL,
    APPROVE_CAMPAIGN, REJECT_CAMPAIGN, APPROVE_SCRIPT,
)


class FounderAction(Base):
    """
    One row per founder decision. Append-only: a later revoke is a new row,
    never an edit of the original approval.
    """
    __tablename__ = "founder_actions"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    draft_id = Column(Integer, nullable=True, index=True)
    channel = Column(String, default="email")          # email | whatsapp | campaign | script
    decision = Column(String, nullable=False)         # APPROVED | REJECTED | REVOKED
    touch = Column(String, nullable=True)             # intro | nudge | proof | ask | breakup
    subject = Column(String, nullable=True)
    recipient = Column(String, nullable=True)
    company = Column(String, nullable=True)
    note = Column(String, nullable=True)
    payload = Column(JSON, nullable=True)
    actor = Column(String, default="FOUNDER", nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


def record(
    db,
    action_type: str,
    *,
    lead=None,
    lead_id: Optional[int] = None,
    draft_id: Optional[int] = None,
    channel: str = "email",
    decision: str,
    touch: str = "",
    subject: str = "",
    recipient: str = "",
    company: str = "",
    note: str = "",
    payload: Optional[dict] = None,
    actor: str = "FOUNDER",
):
    """
    Persist a founder decision. Always FOUNDER (or an explicit human actor).
    Refuses SYSTEM — that is not a founder action by definition.
    """
    if (actor or "").upper() == "SYSTEM":
        raise ValueError(
            "founder_actions refuses actor=SYSTEM — only a human may approve, "
            "reject, or revoke. Automation prepares work; it does not consent."
        )
    if action_type not in FOUNDER_ACTION_TYPES:
        raise ValueError(f"unknown founder action_type: {action_type}")

    lid = lead_id if lead_id is not None else (getattr(lead, "id", None) if lead else None)
    row = FounderAction(
        action_type=action_type,
        lead_id=lid,
        draft_id=draft_id,
        channel=channel,
        decision=decision,
        touch=touch or None,
        subject=subject or None,
        recipient=recipient or (getattr(lead, "email", None) if lead else None),
        company=company or (getattr(lead, "company", None) if lead else None),
        note=note or None,
        payload=payload or {},
        actor=actor,
        occurred_at=datetime.utcnow(),
    )
    db.add(row)
    return row


def approve_outreach(
    lead,
    db,
    *,
    draft_id: Optional[int] = None,
    touch: str = "",
    note: str = "",
    subject: str = "",
    actor: str = "FOUNDER",
) -> dict:
    """
    Founder says yes to one outbound message. Writes:
      1. founder_actions row (audit)
      2. OUTREACH_APPROVED via send_queue (operational signal for the drain)
      3. EmailDraft status → FOUNDER_APPROVED when a draft_id is given
    """
    from app.models.models import EmailDraft
    from app.services import send_queue as sq

    if draft_id:
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
        if draft:
            draft.status = "FOUNDER_APPROVED"
            draft.approved_at = datetime.utcnow()
            if not subject:
                subject = draft.subject or ""
            if not touch:
                touch = draft.follow_up_type or ""

    sq.approve(lead, db, touch=touch, note=note, approved_by=actor)

    row = record(
        db,
        APPROVE_OUTREACH,
        lead=lead,
        draft_id=draft_id,
        channel="email",
        decision="APPROVED",
        touch=touch,
        subject=subject,
        recipient=lead.email,
        company=lead.company,
        note=note,
        payload={"authorised": "one outbound message", "touch": touch},
        actor=actor,
    )
    db.commit()
    return {
        "founder_action_id": row.id,
        "lead_id": lead.id,
        "company": lead.company,
        "decision": "APPROVED",
        "touch": touch,
        "note": "worker will send on next drain cycle",
    }


def reject_outreach(
    lead,
    db,
    *,
    draft_id: Optional[int] = None,
    note: str = "",
    actor: str = "FOUNDER",
) -> dict:
    """Founder says no. Draft is skipped; no send is authorised."""
    from app.models.models import EmailDraft, WorkflowEvent

    if draft_id:
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
        if draft:
            draft.status = "SKIPPED"

    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="OUTREACH_REJECTED",
        actor=actor,
        channel="approval",
        payload={"to": lead.email, "draft_id": draft_id, "note": note},
        occurred_at=datetime.utcnow(),
    ))

    row = record(
        db,
        REJECT_OUTREACH,
        lead=lead,
        draft_id=draft_id,
        channel="email",
        decision="REJECTED",
        recipient=lead.email,
        company=lead.company,
        note=note,
        actor=actor,
    )
    db.commit()
    return {
        "founder_action_id": row.id,
        "lead_id": lead.id,
        "company": lead.company,
        "decision": "REJECTED",
    }


def list_actions(db, limit: int = 50, action_type: str = "") -> list[dict]:
    """Recent founder decisions, newest first — what the audit view reads."""
    q = db.query(FounderAction).order_by(FounderAction.occurred_at.desc())
    if action_type:
        q = q.filter(FounderAction.action_type == action_type)
    rows = q.limit(limit).all()
    return [
        {
            "id": r.id,
            "action_type": r.action_type,
            "decision": r.decision,
            "lead_id": r.lead_id,
            "draft_id": r.draft_id,
            "company": r.company,
            "recipient": r.recipient,
            "channel": r.channel,
            "touch": r.touch,
            "subject": r.subject,
            "note": r.note,
            "actor": r.actor,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        }
        for r in rows
    ]


def pending_for_founder(db, limit: int = 40) -> dict:
    """
    What the founder needs to decide right now.

    Built from EmailDraft rows in DRAFT status that the sequence engine prepared.
    The system already decided these are due and sendable; the founder only
    chooses whether each one goes out.
    """
    from app.models.models import B2BLead, EmailDraft

    drafts = (
        db.query(EmailDraft)
        .filter(EmailDraft.status == "DRAFT")
        .order_by(EmailDraft.created_at.asc())
        .limit(limit)
        .all()
    )
    items = []
    for d in drafts:
        lead = db.query(B2BLead).filter(B2BLead.id == d.lead_id).first()
        items.append({
            "draft_id": d.id,
            "lead_id": d.lead_id,
            "company": lead.company if lead else None,
            "recipient": d.recipient or (lead.email if lead else None),
            "touch": d.follow_up_type,
            "subject": d.subject,
            "body": d.body,
            "reason": d.reason,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "founder_action_required": "APPROVE_OUTREACH or REJECT_OUTREACH",
        })
    return {
        "pending_count": len(items),
        "role": "Founder approves or rejects. The worker sends approved items.",
        "items": items,
    }
