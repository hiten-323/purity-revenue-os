"""
Unit and integration tests for Identifying, Outreach, and Conversion Logic.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.models.models import Base, B2BLead, B2BPayment, EmailDraft, WorkflowEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.high_intent_scorer import evaluate_intent, sweep_intent_scores
from app.services.outreach_engine import build_draft
from app.services.wording_gate import check as check_wording
from app.services.contact_coordinator import get_commercial_checkout_link
from app.services.daily_target_controller import reconcile_shopify_paid_orders, check_daily_b2b_conversion_target


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


# ── 1. Identifying Logic Tests ──────────────────────────────────────────────

def test_identifying_evaluates_coffee_demand_and_competitor_brand():
    lead = B2BLead(
        company="Green Park Cafe",
        city="Jalandhar",
        division="cafe",
        segment="horeca",
        coffee_buying_score=85,
        current_brand="Nescafe Sunrise",
        current_supplier="Nescafe Sunrise",
        qualification_notes="Looking for instant coffee supply; vendor switch evaluation",
        phone="+91-98765-43210",
        email="contact@greenparkcafe.in",
        status="DISCOVERED",
    )
    ev = evaluate_intent(lead)
    assert ev.score >= 80
    assert ev.tier == "Priority A"
    assert "EXPLICIT_COFFEE_BUYING_ENQUIRY" in ev.signals_detected
    assert "SUPPLIER_ENQUIRY" in ev.signals_detected or "COMPETITOR_BRAND_IDENTIFIED" in ev.signals_detected
    assert "HIGH_COFFEE_FIT_SCORE" in ev.signals_detected


def test_identifying_sweep_updates_database_tiers():
    db = _db()
    l1 = B2BLead(
        company="High Intent Distributor",
        division="distributor",
        coffee_buying_score=90,
        current_supplier="Bru",
        qualification_notes="Enquired for instant coffee bulk orders",
        phone="+91-98111-22233",
        email="purchase@distributor.in",
        status="DISCOVERED",
    )
    l2 = B2BLead(
        company="Unknown Hardware Store",
        division="unknown",
        coffee_buying_score=0,
        phone=None,
        email=None,
        status="DISCOVERED",
    )
    db.add_all([l1, l2])
    db.commit()

    res = sweep_intent_scores(db)
    assert res["total"] == 2
    assert res["Priority A"] >= 1

    db.refresh(l1)
    db.refresh(l2)
    assert l1.intent_tier == "Priority A"
    assert l1.intent_score >= 80
    assert l2.intent_tier == "Below 40"


# ── 2. Outreach Logic Tests ─────────────────────────────────────────────────

def test_outreach_draft_contains_starter_carton_and_whatsapp_bridge():
    lead = B2BLead(
        company="City View Hotel",
        city="Amritsar",
        division="hotel",
        phone="+91-98765-43210",
        whatsapp_number="+91-98765-43210",
        email="manager@cityview.in",
        phone_verified=True,
    )

    draft = build_draft(lead, memory={}, sent_count=0, replied=False, days_since_last=None, signature="Hiten Sharma\nFounder")
    
    # Direct purchase starter carton link present
    assert "https://www.p3online.in/products/variety-box" in draft["body"]
    assert "Variety Box (Rs 877)" in draft["body"]
    assert "Gourmet Duo Mini (Rs 570)" in draft["body"]
    
    # WhatsApp consent bridge present
    assert "reply with your WhatsApp number" in draft["body"] or "WhatsApp is more convenient" in draft["body"]

    # Wording gate passes fail-closed
    ok, why = check_wording(draft["subject"], draft["body"])
    assert ok is True, f"Draft failed wording gate: {why}"


def test_outreach_followup_also_passes_wording_gate():
    lead = B2BLead(
        company="Grand Plaza Restaurant",
        city="Bathinda",
        division="restaurant",
        phone="+91-98765-43210",
        email="fnb@grandplaza.in",
    )

    draft = build_draft(lead, memory={"current_supplier": "Nescafe"}, sent_count=1, replied=False, days_since_last=4, signature="Hiten Sharma\nFounder")
    
    assert "https://www.p3online.in/products/variety-box" in draft["body"]
    ok, why = check_wording(draft["subject"], draft["body"])
    assert ok is True, f"Follow-up failed wording gate: {why}"


# ── 3. Conversion Logic Tests ───────────────────────────────────────────────

def test_conversion_commercial_checkout_link_accurate():
    link_info = get_commercial_checkout_link("variety-box")
    assert link_info["price_inr"] == 877.0
    assert link_info["url"] == "https://www.p3online.in/products/variety-box"

    mini_info = get_commercial_checkout_link("gourmet-duo-mini")
    assert mini_info["price_inr"] == 570.0
    assert mini_info["url"] == "https://www.p3online.in/products/gourmet-duo-mini"


def test_shopify_paid_orders_never_convert_b2b_leads():
    db = _db()
    lead = B2BLead(
        company="Metro Wholesale Traders",
        email="accounts@metrowholesale.in",
        phone="+91-98765-12345",
        status="PROPOSAL_SENT",
        email_trust="HIGH",
        email_verified=True,
        email_verification_status="VALID",
        contact_confidence=0.85,
    )
    db.add(lead)
    db.commit()
    draft = EmailDraft(
        lead_id=lead.id,
        recipient="accounts@metrowholesale.in",
        follow_up_type="catalogue_followup",
        status="PENDING",
    )
    db.add(draft)
    db.commit()
    # Simulate an already-existing pending draft; admission listeners run on insert.
    draft.status = "PENDING"
    db.commit()

    with patch("app.services.daily_target_controller.fetch_shopify_paid_orders_today") as mock_fetch:
        result = reconcile_shopify_paid_orders(db)
        mock_fetch.assert_not_called()

    assert result["status"] == "excluded_from_b2b"
    db.refresh(lead)
    db.refresh(draft)
    assert lead.status == "PROPOSAL_SENT"
    assert draft.status == "PENDING"
    assert db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "PAID_ORDER_CONVERTED"
    ).count() == 0


def test_b2b_daily_conversion_counts_only_verified_b2b_cash():
    db = _db()
    lead = B2BLead(company="North Star Cafe", status="PAYMENT_VERIFIED")
    db.add(lead)
    db.commit()
    db.add_all([
        WorkflowEvent(
            lead_id=lead.id, event_type="PAYMENT_VERIFIED", actor="FOUNDER",
            channel="founder", payload={"actual_cash": 1250.0},
            occurred_at=datetime.utcnow(),
        ),
        WorkflowEvent(
            lead_id=lead.id, event_type="PAID_ORDER_CONVERTED", actor="SHOPIFY",
            channel="shopify", payload={"amount": 877.0},
            occurred_at=datetime.utcnow(),
        ),
    ])
    db.commit()

    # A PAYMENT_VERIFIED event by itself is not authoritative cash evidence.
    status_without_ledger = check_daily_b2b_conversion_target(db)
    assert status_without_ledger.target_met is False
    assert status_without_ledger.paid_orders_count == 0

    # Two partial collections for one B2B account count as one conversion.
    db.add_all([
        B2BPayment(
            lead_id=lead.id, payment_reference='UTR-B2B-TEST-001',
            amount_inr=1250.0, currency='INR', status='FOUNDER_VERIFIED',
            recorded_by='FOUNDER', recorded_at=datetime.utcnow(),
        ),
        B2BPayment(
            lead_id=lead.id, payment_reference='UTR-B2B-TEST-002',
            amount_inr=200.0, currency='INR', status='FOUNDER_VERIFIED',
            recorded_by='FOUNDER', recorded_at=datetime.utcnow(),
        ),
    ])
    db.commit()

    status = check_daily_b2b_conversion_target(db)
    assert status.target_met is True
    assert status.paid_orders_count == 1
    assert status.verified_revenue_inr == 1450.0
    assert status.verified_order_ids == [f"B2B-LEAD-{lead.id}"]
