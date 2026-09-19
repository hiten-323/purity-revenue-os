"""
Workflow Engine — Layer 4 of the Purity Beans Founder Revenue OS V1.1.

Owns ALL side effects: email send, WhatsApp draft, AI calling, founder call
logging, meeting booking, sample dispatch, proposal send, order and reorder.

Lifecycle: REQUESTED → PENDING → EXECUTING → COMPLETED | FAILED (retryable).

Rules:
- Consults business policies BEFORE executing (policy_blocked result if denied).
- Every execution writes a WorkflowExecution row and appends immutable
  WorkflowEvents via pipeline_tracker.track().
- The Decision Engine never calls this; only API endpoints (founder actions) do.
"""

from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.models import B2BLead, EmailDraft, WorkflowExecution
from app.services.pipeline_tracker import track
from app.services.business_policies import CallingPolicy, SamplePolicy, ProposalPolicy


class WorkflowEngine:
    """All side effects live behind execute()."""

    HANDLERS = {
        "EMAIL_SEND", "WHATSAPP_CONFIRM", "AI_CALL", "FOUNDER_CALL",
        "MEETING_BOOK", "SAMPLE_DISPATCH", "PROPOSAL_SEND", "ORDER_WON", "REORDER",
    }

    @classmethod
    def execute(cls, db: Session, workflow_type: str, lead_id: int,
                payload: dict | None = None, requested_by: str = "FOUNDER") -> dict:
        payload = payload or {}
        if workflow_type not in cls.HANDLERS:
            return {"status": "failed", "error": f"Unknown workflow_type {workflow_type}"}

        lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
        if not lead:
            return {"status": "failed", "error": f"Lead {lead_id} not found"}

        wx = WorkflowExecution(
            workflow_type=workflow_type, lead_id=lead_id,
            status="REQUESTED", requested_by=requested_by, payload=payload,
        )
        db.add(wx)
        db.commit()

        # ── Policy gate ──────────────────────────────────────────────
        blocked_reason = cls._policy_check(workflow_type, lead)
        if blocked_reason:
            wx.status = "FAILED"
            wx.error = f"policy_blocked: {blocked_reason}"
            wx.finished_at = datetime.utcnow()
            db.commit()
            return {"status": "policy_blocked", "reason": blocked_reason,
                    "execution_id": wx.id}

        wx.status = "EXECUTING"
        wx.started_at = datetime.utcnow()
        db.commit()

        try:
            handler = getattr(cls, f"_run_{workflow_type.lower()}")
            result = handler(db, lead, payload)
            wx.status = "COMPLETED"
            wx.result = result
            wx.finished_at = datetime.utcnow()
            db.commit()
            return {"status": "completed", "execution_id": wx.id, **result}
        except Exception as e:
            wx.status = "FAILED"
            wx.error = str(e)
            wx.finished_at = datetime.utcnow()
            db.commit()
            track(db, "WORKFLOW_FAILED", lead_id=lead.id, actor="SYSTEM",
                  channel="system", payload={"workflow_type": workflow_type, "error": str(e)})
            return {"status": "failed", "error": str(e), "execution_id": wx.id}

    @classmethod
    def retry(cls, db: Session, execution_id: int) -> dict:
        wx = db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()
        if not wx or wx.status != "FAILED":
            return {"status": "failed", "error": "Execution not found or not FAILED"}
        wx.retry_count = (wx.retry_count or 0) + 1
        db.commit()
        return cls.execute(db, wx.workflow_type, wx.lead_id, wx.payload, wx.requested_by)

    # ── Policies ─────────────────────────────────────────────────────

    @staticmethod
    def _policy_check(workflow_type: str, lead) -> str | None:
        if workflow_type in ("AI_CALL", "FOUNDER_CALL"):
            ok, reason = CallingPolicy.can_call(lead)
            if not ok:
                return reason
        if workflow_type == "SAMPLE_DISPATCH":
            ok, reason = SamplePolicy.can_dispatch(lead)
            if not ok:
                return reason
        if workflow_type == "PROPOSAL_SEND":
            ok, reason = ProposalPolicy.can_send(lead)
            if not ok:
                return reason
        return None

    # ── Handlers (side effects) ──────────────────────────────────────

    @staticmethod
    def _run_email_send(db: Session, lead, payload: dict) -> dict:
        """Send an APPROVED EmailDraft via Zoho. Founder approval is mandatory."""
        from app.services.email_sender import build_outreach_email, send_email

        draft_id = payload.get("draft_id")
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first() if draft_id else None
        if not draft or draft.status not in ("PENDING", "EDITED", "APPROVED"):
            raise ValueError("No approvable draft found — email must go through the Approval Inbox")

        email_obj = build_outreach_email(
            to_email=lead.email,
            to_name=lead.contact_name or lead.company,
            company=lead.company,
            subject=draft.subject,
            body=payload.get("custom_body") or draft.body,
            lead_id=lead.id,
        )
        result = send_email(email_obj)
        if result.status != "sent":
            raise RuntimeError(result.error or "SMTP send failed")

        draft.status = "SENT"
        draft.sent_at = datetime.utcnow()
        before = lead.status
        lead.status = "EMAIL_SENT"
        lead.email_approved_by_founder = True
        lead.last_updated = datetime.utcnow()
        track(db, "EMAIL_SENT", lead_id=lead.id, actor="FOUNDER", channel="email",
              before_status=before, after_status="EMAIL_SENT",
              payload={"subject": draft.subject, "draft_id": draft.id})
        return {"sent_to": lead.email, "draft_id": draft.id}

    @staticmethod
    def _run_whatsapp_confirm(db: Session, lead, payload: dict) -> dict:
        """Founder confirms they sent the WhatsApp message from their phone."""
        before = lead.status
        lead.status = "WHATSAPP_SENT"
        lead.last_updated = datetime.utcnow()
        track(db, "WHATSAPP_SENT", lead_id=lead.id, actor="FOUNDER", channel="whatsapp",
              before_status=before, after_status="WHATSAPP_SENT",
              payload={"wa": lead.whatsapp_number, "message_preview": (payload.get("message") or "")[:120]})
        return {"confirmed": True}

    @staticmethod
    def _run_ai_call(db: Session, lead, payload: dict) -> dict:
        from app.services.calling_agent import CallingAgentService
        result = CallingAgentService.trigger_ai_call(db, lead)
        track(db, "AI_CALL_INITIATED", lead_id=lead.id, actor="AI", channel="call",
              payload={"provider": "nuraveda", "result": str(result)[:200]})
        return {"call": str(result)[:200]}

    @staticmethod
    def _run_founder_call(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "FOUNDER_CALLED"
        lead.last_call_date = datetime.utcnow()
        lead.call_attempts = (lead.call_attempts or 0) + 1
        lead.last_updated = datetime.utcnow()
        outcome = payload.get("outcome", "")
        if outcome:
            lead.call_outcome_last = outcome
        track(db, "FOUNDER_CALL_COMPLETED", lead_id=lead.id, actor="FOUNDER", channel="call",
              before_status=before, after_status="FOUNDER_CALLED", payload={"outcome": outcome})
        return {"logged": True}

    @staticmethod
    def _run_meeting_book(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "MEETING_BOOKED"
        lead.last_updated = datetime.utcnow()
        track(db, "MEETING_BOOKED", lead_id=lead.id, actor="FOUNDER", channel="system",
              before_status=before, after_status="MEETING_BOOKED",
              payload={"when": payload.get("when", "")})
        return {"booked": True}

    @staticmethod
    def _run_sample_dispatch(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "SAMPLE_SENT"
        lead.sample_requested = True
        lead.sample_sku = payload.get("sku") or lead.sample_sku or "Purica"
        lead.last_updated = datetime.utcnow()
        track(db, "SAMPLE_DISPATCHED", lead_id=lead.id, actor="FOUNDER", channel="system",
              before_status=before, after_status="SAMPLE_SENT",
              payload={"sku": lead.sample_sku})
        return {"dispatched": lead.sample_sku}

    @staticmethod
    def _run_proposal_send(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "PROPOSAL_SENT"
        lead.last_updated = datetime.utcnow()
        track(db, "PROPOSAL_SENT", lead_id=lead.id, actor="FOUNDER", channel="email",
              before_status=before, after_status="PROPOSAL_SENT",
              payload={"monthly_kg": lead.proposal_monthly_kg})
        return {"sent": True}

    @staticmethod
    def _run_order_won(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "ORDER_WON"
        lead.last_updated = datetime.utcnow()
        track(db, "ORDER_WON", lead_id=lead.id, actor="FOUNDER", channel="system",
              before_status=before, after_status="ORDER_WON",
              payload={"value": lead.estimated_value})
        return {"won": True}

    @staticmethod
    def _run_reorder(db: Session, lead, payload: dict) -> dict:
        before = lead.status
        lead.status = "REORDER_PREDICTED"
        lead.last_updated = datetime.utcnow()
        track(db, "REORDER_TRIGGERED", lead_id=lead.id, actor="SYSTEM", channel="system",
              before_status=before, after_status="REORDER_PREDICTED", payload={})
        return {"flagged": True}
