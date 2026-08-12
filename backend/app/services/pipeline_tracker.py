"""
Pipeline Tracker — immutable event log for every touchpoint in the sales workflow.

Usage:
    from app.services.pipeline_tracker import track
    track(db, lead_id=lead.id, event_type="EMAIL_SENT", actor="AI",
          channel="email", payload={"subject": "...", "to": lead.email})

Event types (standard):
    LEAD_DISCOVERED, LEAD_ENRICHED,
    EMAIL_VERIFIED, EMAIL_APPROVED, EMAIL_REJECTED,
    EMAIL_SENT, EMAIL_OPENED, EMAIL_CLICKED, EMAIL_REPLIED, EMAIL_BOUNCED,
    PHONE_ENRICHED, WHATSAPP_SENT, WHATSAPP_DELIVERED, WHATSAPP_REPLIED,
    AI_CALL_INITIATED, AI_CALL_CONNECTED, AI_CALL_COMPLETED, AI_CALL_FAILED,
    FOUNDER_CALL_COMPLETED, MEETING_BOOKED, MEETING_COMPLETED,
    SAMPLE_REQUESTED, SAMPLE_DISPATCHED, SAMPLE_FEEDBACK,
    PROPOSAL_SENT, PROPOSAL_VIEWED, ORDER_WON, PAYMENT_RECEIVED, REORDER_TRIGGERED,
    STATUS_CHANGED, DNC_SET, STAGE_STALLED, WORKFLOW_FAILED
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import WorkflowEvent, B2BLead


def track(
    db: Session,
    event_type: str,
    lead_id: int | None = None,
    actor: str = "SYSTEM",
    channel: str | None = None,
    before_status: str | None = None,
    after_status: str | None = None,
    payload: dict | None = None,
    workflow_id: str | None = None,
    commit: bool = False,
) -> WorkflowEvent:
    """Append an immutable event. Returns the event (not yet committed unless commit=True)."""
    ev = WorkflowEvent(
        event_type=event_type,
        lead_id=lead_id,
        actor=actor,
        channel=channel,
        before_status=before_status,
        after_status=after_status,
        payload=payload or {},
        workflow_id=workflow_id,
        occurred_at=datetime.utcnow(),
    )
    db.add(ev)
    if commit:
        db.commit()
    return ev


def get_lead_timeline(db: Session, lead_id: int) -> list[dict]:
    """Return the full event timeline for a lead, newest first."""
    events = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.lead_id == lead_id)
        .order_by(WorkflowEvent.occurred_at.desc())
        .all()
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type,
            "actor": e.actor,
            "channel": e.channel,
            "before_status": e.before_status,
            "after_status": e.after_status,
            "payload": e.payload,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
        }
        for e in events
    ]


def get_funnel_stats(db: Session) -> dict:
    """
    Compute conversion rates across the full pipeline funnel.
    Returns counts and rates for the dashboard.
    """
    from sqlalchemy import func

    total_leads = db.query(B2BLead).count()
    if total_leads == 0:
        return _empty_funnel()

    def _count(event: str) -> int:
        return (
            db.query(func.count(func.distinct(WorkflowEvent.lead_id)))
            .filter(WorkflowEvent.event_type == event)
            .scalar()
            or 0
        )

    def _status_count(*statuses) -> int:
        return db.query(B2BLead).filter(B2BLead.status.in_(statuses)).count()

    discovered     = total_leads
    email_verified = db.query(B2BLead).filter(
        B2BLead.email_verification_status.in_(["VALID", "CATCH_ALL"])
    ).count()
    email_sent     = _status_count("EMAIL_SENT", "INTRO_EMAIL_SENT", "REPLIED", "MEETING_BOOKED",
                                   "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON")
    email_opened   = db.query(B2BLead).filter(B2BLead.email_opens > 0).count()
    replied        = _status_count("REPLIED")
    meeting_booked = _status_count("MEETING_BOOKED", "MEETING_COMPLETED")
    sample_sent    = _status_count("SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON")
    proposal_sent  = _status_count("PROPOSAL_SENT", "ORDER_WON")
    order_won      = _status_count("ORDER_WON", "ONBOARDED", "REORDER_PREDICTED")
    with_phone     = db.query(B2BLead).filter(
        B2BLead.phone.isnot(None), B2BLead.phone != ""
    ).count()
    with_whatsapp  = db.query(B2BLead).filter(
        B2BLead.whatsapp_number.isnot(None), B2BLead.whatsapp_number != ""
    ).count()
    with_email     = db.query(B2BLead).filter(
        B2BLead.email.isnot(None), B2BLead.email.like("%@%")
    ).count()

    def pct(num: int, den: int) -> float:
        return round(num / den * 100, 1) if den > 0 else 0.0

    return {
        "funnel": [
            {"stage": "Lead Discovered",    "count": discovered,     "pct": 100.0,                           "color": "gray"},
            {"stage": "Email Found",         "count": with_email,     "pct": pct(with_email, discovered),     "color": "blue"},
            {"stage": "Email Verified",      "count": email_verified, "pct": pct(email_verified, with_email), "color": "cyan"},
            {"stage": "Email Sent",          "count": email_sent,     "pct": pct(email_sent, email_verified), "color": "amber"},
            {"stage": "Email Opened",        "count": email_opened,   "pct": pct(email_opened, email_sent),   "color": "yellow"},
            {"stage": "Replied",             "count": replied,        "pct": pct(replied, email_sent),        "color": "purple"},
            {"stage": "Meeting Booked",      "count": meeting_booked, "pct": pct(meeting_booked, replied),    "color": "indigo"},
            {"stage": "Sample Sent",         "count": sample_sent,    "pct": pct(sample_sent, meeting_booked),"color": "teal"},
            {"stage": "Proposal Sent",       "count": proposal_sent,  "pct": pct(proposal_sent, sample_sent), "color": "green"},
            {"stage": "Order Won",           "count": order_won,      "pct": pct(order_won, proposal_sent),   "color": "emerald"},
        ],
        "kpis": {
            "total_leads":            discovered,
            "with_email":             with_email,
            "with_phone":             with_phone,
            "with_whatsapp":          with_whatsapp,
            "email_found_pct":        pct(with_email, discovered),
            "email_verified_pct":     pct(email_verified, with_email),
            "email_sent_pct":         pct(email_sent, email_verified),
            "email_open_rate":        pct(email_opened, email_sent),
            "reply_rate":             pct(replied, email_sent),
            "meeting_rate":           pct(meeting_booked, replied),
            "sample_conversion":      pct(sample_sent, meeting_booked),
            "proposal_conversion":    pct(proposal_sent, sample_sent),
            "order_conversion":       pct(order_won, proposal_sent),
            "end_to_end_conversion":  pct(order_won, discovered),
        },
    }


def _empty_funnel() -> dict:
    stages = [
        "Lead Discovered", "Email Found", "Email Verified", "Email Sent",
        "Email Opened", "Replied", "Meeting Booked", "Sample Sent",
        "Proposal Sent", "Order Won",
    ]
    return {
        "funnel": [{"stage": s, "count": 0, "pct": 0.0, "color": "gray"} for s in stages],
        "kpis": {},
    }
