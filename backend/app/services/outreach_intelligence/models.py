"""ORM models for Outreach Intelligence V1 (smart_outreach-style local models)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint,
)
from app.database.database import Base


class OutreachEvent(Base):
    """Append-only outreach event ledger. Never UPDATE historical rows."""
    __tablename__ = "outreach_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    company_id = Column(Integer, nullable=True, index=True)
    company = Column(String, nullable=True)
    channel = Column(String, nullable=True, index=True)  # email|call|system|null
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    campaign_source = Column(String, nullable=True)
    message_version = Column(String, nullable=True)
    template_version_id = Column(String, nullable=True)
    personalization_json = Column(JSON, nullable=True)
    delivery_status = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)
    response_status = Column(String, nullable=True)
    outcome = Column(String, nullable=True, index=True)
    confidence = Column(String, nullable=True)  # OBSERVED|INFERRED|LOW_CONFIDENCE
    next_action = Column(String, nullable=True)
    model_version = Column(String, nullable=True, default="oi-v1")
    metadata_json = Column(JSON, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OutreachExperienceAggregate(Base):
    """Segment experience rollup. Updated from OBSERVED outcomes only."""
    __tablename__ = "outreach_experience_aggregates"
    __table_args__ = (
        UniqueConstraint("segment_key", "channel", "metric", name="uq_oi_exp_seg_ch_metric"),
    )

    id = Column(Integer, primary_key=True)
    segment_key = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    metric = Column(String, nullable=False, index=True)
    value = Column(Float, default=0.0, nullable=False)
    sample_size = Column(Integer, default=0, nullable=False)
    wins = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OutreachOutcomeCorrection(Base):
    """Human correction of an automated classification/outcome."""
    __tablename__ = "outreach_outcome_corrections"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey("outreach_events.id"), nullable=True, index=True)
    field = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    corrected_by = Column(String, nullable=True, default="FOUNDER")
    corrected_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OutreachExperiment(Base):
    """Track-only experiment scaffold. DISABLED unless explicitly enabled."""
    __tablename__ = "outreach_experiments"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    hypothesis = Column(Text, nullable=True)
    status = Column(String, default="DISABLED", nullable=False)
    variants_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OutreachExperimentAssignment(Base):
    __tablename__ = "outreach_experiment_assignments"
    __table_args__ = (
        UniqueConstraint("experiment_id", "lead_id", name="uq_oi_experiment_lead"),
    )

    id = Column(Integer, primary_key=True)
    experiment_id = Column(Integer, ForeignKey("outreach_experiments.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    variant = Column(String, nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    active = Column(Boolean, default=False, nullable=False)
