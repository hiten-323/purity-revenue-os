import os
"""
Comprehensive Test Suite for Purity Revenue OS V2.0 Innovations:
1. Speed-to-Lead Inbound Engine (<60s SLA, Multi-Touch Cadence, 1-Click COD link) - Bashar Jkatou model
2. Persistent Long-Term Memory (claude-mem model)
3. Agent Governance & Financial Budgets (Paperclip model)
"""
import uuid
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from app.database.database import SessionLocal
from app.models.models import B2BLead, LeadInteraction, ApprovalRequest
from app.services.inbound_lead_engine import InboundSpeedToLeadEngine, InboundLeadPayload
from app.services.persistent_memory import PersistentMemoryEngine
from app.services.agent_governance import AgentGovernanceEngine
from app.main import app

client = TestClient(app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_b2b_inbound_captures_interest_without_implicit_outreach_consent(db):
    """B2B form interest must not imply phone verification or channel permission."""
    u_id = uuid.uuid4().hex[:6]
    phone_digits = f"9811{int(u_id, 16) % 1000000:06d}"
    payload = InboundLeadPayload(
        name="Vikram Malhotra",
        phone=phone_digits,
        email=f"vikram_{u_id}@artisanroasters.in",
        company=f"Artisan Roastery Cafe {u_id}",
        city="Bengaluru",
        source="meta_lead_ad",
        inquiry_text="Looking for premium 100% Arabica freeze dried coffee for our 4 cafes. Zero chicory required.",
        preferred_format="50g glass jar",
        business_type="cafe",
    )
    result = InboundSpeedToLeadEngine.process_inbound_lead(payload, db)
    assert result.status == "B2B_REVIEW_REQUIRED"
    assert result.lead_id > 0
    assert result.recommended_product == "B2B_PRICEBOOK_PENDING"
    assert result.cadence == []
    assert result.governance_approved is False
    assert "No outbound message or call has been sent" in result.immediate_message_draft
    lead = db.query(B2BLead).filter(B2BLead.id == result.lead_id).first()
    assert lead.consent_status == "UNKNOWN"
    assert lead.contact_status == "NEEDS_RESEARCH"
    assert lead.phone_verified is False
    assert lead.whatsapp_verified is False
    assert lead.email_verified is False
    assert lead.phone_trust == "DISCOVERED"
    assert lead.email_trust == "DISCOVERED"
    assert lead.intent_tier == "PRIORITY_A"
    assert lead.intent_score >= 90


def test_inbound_consent_requires_signed_source_and_is_channel_specific(db):
    from app.models.models import WorkflowEvent
    u_id = uuid.uuid4().hex[:6]
    payload = InboundLeadPayload(
        name="Neha Sharma",
        phone=f"9822{int(u_id, 16) % 1000000:06d}",
        email=f"neha_{u_id}@example.org",
        company=f"Neha Cafe {u_id}",
        source="website_form",
        inquiry_text="Please send wholesale pricing for my cafe.",
        business_type="cafe",
        consent_explicit=True,
        consent_text="I agree to receive phone calls and WhatsApp messages about wholesale offers.",
        consent_channels=["voice", "whatsapp"],
        consent_signature_valid=False,
    )
    result = InboundSpeedToLeadEngine.process_inbound_lead(payload, db)
    lead = db.query(B2BLead).filter(B2BLead.id == result.lead_id).first()
    assert result.status == "B2B_REVIEW_REQUIRED"
    assert lead.consent_status == "UNKNOWN"
    assert db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type.in_([
            "CALL_AUTHORIZATION_RECORDED",
            "WHATSAPP_CONSENT_RECORDED",
            "EMAIL_AUTHORIZATION_RECORDED",
        ]),
    ).count() == 0


def test_persistent_memory_claude_mem_retrieval(db):
    """Verifies that facts extracted from text persist across sessions and form a coherent brief."""
    u_id = uuid.uuid4().hex[:6]
    phone_digits = f"9877{int(u_id, 16) % 1000000:06d}"
    lead = B2BLead(
        company=f"Greenleaf Hotel {u_id}",
        contact_name="Sunil Mehra",
        phone=phone_digits,
        city="Shimla",
        status="PROSPECT"
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    raw_interaction = (
        "We are currently serving Nescafe Classic in 40 rooms. "
        "Looking for strong Robusta without chicory. "
        "Need single-serve sachets or 50g glass jars. "
        "Must have GST input credit invoice. Can we pay COD?"
    )

    extracted = PersistentMemoryEngine.extract_and_store_memories(
        lead_id=lead.id,
        raw_text=raw_interaction,
        interaction_method="phone_transcript",
        db=db,
        actor="TEST_AGENT"
    )

    assert len(extracted) >= 4
    categories = {f.category for f in extracted}
    assert "COFFEE_PREFERENCE" in categories
    assert "COMMERCIAL_TERMS" in categories
    assert "OBJECTIONS_HANDLED" in categories

    # Verify distilled memory context retrieval
    context = PersistentMemoryEngine.get_condensed_memory_context(lead.id, db)
    assert f"Greenleaf Hotel {u_id}" in context
    assert "Nescafe" in context or "Robusta" in context
    assert "GST" in context or "COD" in context


def test_agent_governance_budget_and_approval_thresholds(db):
    """Verifies Paperclip-style spend limit checks and founder approval gating."""
    u_id = uuid.uuid4().hex[:6]
    phone_digits = f"9988{int(u_id, 16) % 1000000:06d}"
    lead = B2BLead(
        company=f"Apex Wholesale {u_id}",
        contact_name="Rajesh Gupta",
        phone=phone_digits,
        status="PROSPECT"
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # 1. Standard sample order (< ₹10,000, 0% discount, 0 days credit) -> Auto-approved
    auto_eval = AgentGovernanceEngine.evaluate_action_for_approval(
        action_type="sample_dispatch",
        lead_id=lead.id,
        order_value_inr=570.0,
        discount_pct=0.0,
        credit_days=0,
        evidence_summary="Gourmet duo sample dispatch",
        db=db
    )
    assert auto_eval["approved"] is True
    assert auto_eval["status"] == "AUTO_APPROVED"

    # 2. Large bulk order (> ₹10,000 with 30 days credit) -> Requires Founder Approval
    gated_eval = AgentGovernanceEngine.evaluate_action_for_approval(
        action_type="wholesale_quote",
        lead_id=lead.id,
        order_value_inr=25000.0,
        discount_pct=28.75,
        credit_days=30,
        evidence_summary="Distributor request 100kg",
        db=db
    )
    assert gated_eval["approved"] is False
    assert gated_eval["status"] == "PENDING_FOUNDER"
    req_id = gated_eval["approval_request_id"]
    assert req_id is not None

    # Verify ApprovalRequest in DB
    req = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
    assert req.status == "PENDING_FOUNDER"
    assert req.expected_revenue == 25000.0

    # 3. Founder Decision Processing
    decision_res = AgentGovernanceEngine.process_founder_decision(
        request_id=req_id,
        decision="APPROVED",
        founder_notes="Approved conditional on 50% advance payment.",
        db=db
    )
    assert decision_res["success"] is True
    assert decision_res["status"] == "APPROVED"

    req_updated = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
    assert req_updated.status == "APPROVED"
    assert "FOUNDER_APPROVED" in req_updated.draft_subject


def test_api_inbound_webhook_endpoint(db):
    """Tests POST /api/v1/inbound/lead-webhook through FastAPI test client."""
    import hmac
    import hashlib
    import json
    u_id = uuid.uuid4().hex[:6]
    phone_digits = f"9833{int(u_id, 16) % 1000000:06d}"
    payload = {
        "name": "Ananya Roy",
        "phone": phone_digits,
        "email": f"ananya_{u_id}@roycafe.com",
        "company": f"Roy Specialty Cafe {u_id}",
        "city": "Kolkata",
        "source": "website_form",
        "inquiry_text": "Need chicory blended instant coffee for South Indian style filter coffee profile.",
        "business_type": "cafe"
    }
    raw = json.dumps(payload).encode("utf-8")
    secret = (os.getenv("INBOUND_WEBHOOK_SECRET") or "").strip()
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "X-Inbound-Signature": f"sha256={sig}"}

    resp = client.post("/api/v1/inbound/lead-webhook", content=raw, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert u_id in data["company"]
    assert data["status"] == "B2B_REVIEW_REQUIRED"
    assert data["recommended_product"] == "B2B_PRICEBOOK_PENDING"
    assert data["cadence_steps_count"] == 0
    assert data["governance_approved"] is False


def test_api_governance_status_and_approvals(db):
    """Tests GET /api/v1/governance/status and /api/v1/governance/approvals."""
    resp = client.get("/api/v1/governance/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "daily_budget_inr" in data
    assert "spent_today_inr" in data
    assert "kill_switch_active" in data

    resp_app = client.get("/api/v1/governance/approvals")
    assert resp_app.status_code == 200
    assert "pending_approvals" in resp_app.json()

