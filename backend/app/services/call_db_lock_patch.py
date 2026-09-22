"""SQLite lock mitigations for AI call placement (multi-process).

Imported by worker / boot patches. Replaces
CallingAgentService._place_qualification_call so the DB session is not held
across the SIP/HTTP dial, and post-dial lead UPDATEs use commit_with_retry.
"""
from __future__ import annotations

from datetime import datetime


def apply_call_placement_lock_fix() -> None:
    from app.services.calling_agent import CallingAgentService
    if getattr(CallingAgentService, "_LOCK_FIX_APPLIED", False):
        return

    def _place_qualification_call(db, lead, pipeline, scheduled_at=None):
        from app.services import voice_quality
        from app.services import voice_router
        from app.services.outreach_learning import build_learning_context
        from app.database.database import commit_with_retry

        # Release any open write txn before network dial.
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

        learning = build_learning_context(db, lead)
        questions = list(pipeline.QUALIFICATION_QUESTIONS)
        learned_question = learning.get("recommended_call_question")
        if learned_question and learned_question not in questions:
            questions.append(learned_question)

        result = voice_router.place_call(
            lead,
            context={
                "opening": pipeline.opening_for(lead),
                "questions": questions,
                "learning": {
                    "summary": learning.get("summary", ""),
                    "recommended_question": learned_question or "",
                    "top_objection": learning.get("top_objection"),
                    "evidence_count": learning.get("evidence_count", 0),
                },
                "constraints": voice_quality.constraints_for_dispatch(
                    pipeline.CALL_CONSTRAINTS
                ),
                "handoff_topics": list(pipeline.HANDOFF_TOPICS),
                **pipeline.call_context(lead, db),
            },
            scheduled_at=scheduled_at,
        )
        if not result.placed:
            return False, f"provider_refused: {result.error}"

        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id,
            event_type="AI_CALL_LEARNING_CONTEXT",
            actor="SMART_OUTREACH",
            channel="call",
            payload={
                "summary": learning.get("summary", ""),
                "recommended_question": learned_question or "",
                "top_objection": learning.get("top_objection"),
                "evidence_count": learning.get("evidence_count", 0),
                "category": learning.get("category"),
            },
            occurred_at=datetime.utcnow(),
        ))
        pipeline.advance(
            lead, db, pipeline.AI_CALL_ATTEMPTED,
            note="disclosed AI qualification call placed",
        )
        lead.ai_call_count = (lead.ai_call_count or 0) + 1
        lead.call_status = "CALLING"
        lead.call_provider = voice_router.active()
        lead.last_call_date = datetime.utcnow()
        commit_with_retry(db)
        return True, f"qualification_call_placed: {result.provider_call_id}"

    CallingAgentService._place_qualification_call = staticmethod(_place_qualification_call)
    CallingAgentService._LOCK_FIX_APPLIED = True


try:
    apply_call_placement_lock_fix()
except Exception:
    pass
