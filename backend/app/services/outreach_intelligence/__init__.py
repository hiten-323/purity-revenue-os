"""Outreach Intelligence V1 — learn from contactable leads, advise the next similar one.

Safety: advisory only; never grants eligibility, never suppresses channels, never dials/sends.
Email + AI call remain independently eligible (BOTH). WhatsApp stays off.
"""
from app.services.outreach_intelligence.models import (
    OutreachEvent,
    OutreachExperienceAggregate,
    OutreachOutcomeCorrection,
    OutreachExperiment,
    OutreachExperimentAssignment,
)
from app.services.outreach_intelligence.event_ledger import (
    record_event,
    sync_from_existing,
    ensure_outreach_intelligence_schema,
)
from app.services.outreach_intelligence.experience_store import (
    build_lead_outreach_profile,
    retrieve_similar_lead_experience,
    MIN_EVIDENCE,
)
from app.services.outreach_intelligence.learning_loop import (
    on_outreach_event,
    recalculate_segment_stats,
    apply_correction,
)
from app.services.outreach_intelligence.report import build_intelligence_report
from app.services.outreach_intelligence.boot import register_outreach_intelligence
from app.services.outreach_intelligence.outcomes import (
    classify_email_reply,
    extract_call_commercial_signals,
    outcome_level,
)

__all__ = [
    "OutreachEvent",
    "OutreachExperienceAggregate",
    "OutreachOutcomeCorrection",
    "OutreachExperiment",
    "OutreachExperimentAssignment",
    "record_event",
    "sync_from_existing",
    "ensure_outreach_intelligence_schema",
    "build_lead_outreach_profile",
    "retrieve_similar_lead_experience",
    "MIN_EVIDENCE",
    "on_outreach_event",
    "recalculate_segment_stats",
    "apply_correction",
    "build_intelligence_report",
    "classify_email_reply",
    "extract_call_commercial_signals",
    "outcome_level",
    "register_outreach_intelligence",
]
