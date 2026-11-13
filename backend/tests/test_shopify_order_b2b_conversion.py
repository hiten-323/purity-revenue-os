"""
Test suite verifying that order creation triggers B2B purchase handling:
1. Matching B2B lead by phone stops cold outreach, cancels drafts/callbacks, and transitions to PAID_CONVERSION.
2. Matching B2B lead by email stops cold outreach.
3. Retail D2C orders without matching B2B lead do not corrupt B2B records.
4. B2B mark_won endpoint cancels queued touches and records conversion.
"""
import uuid
import pytest
import asyncio
from datetime import datetime, timedelta

from app.database.database import SessionLocal
from app.models.models import (
    B2BLead, EmailDraft, OutboundWhatsApp, ScheduledCallback, WorkflowEvent,
)
from app.api.endpoints import _route_shopify_event, mark_won


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_b2b_lead(db, phone=None, email=None):
    u = uuid.uuid4().hex[:6]
    phone = phone or f"98765{int(u, 16) % 100000:05d}"
    email = email or f"cafe_{u}@example.com"
    lead = B2BLead(
        company=f"Conversion Cafe {u}",
        contact_name="Aarav Sharma",
        phone=phone,
        email=email,
        status="IN_CADENCE",
        lead_owner="Hiten",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Attach open email draft
    draft = EmailDraft(
        lead_id=lead.id,
        recipient=email,
        subject="Sample follow up",
        body="Following up on sample",
        status="QUEUED",
    )
    db.add(draft)

    # Attach queued WhatsApp message
    wa = OutboundWhatsApp(
        lead_id=lead.id,
        recipient=phone,
        message="Special wholesale pricing",
        status="QUEUED",
    )
    db.add(wa)

    # Attach scheduled callback
    cb = ScheduledCallback(
        lead_id=lead.id,
        callback_requested=True,
        callback_window_start=datetime.utcnow() + timedelta(days=1),
        callback_status="CALLBACK_SCHEDULED",
        original_customer_statement="Call me tomorrow",
    )
    db.add(cb)

    db.commit()
    db.refresh(lead)
    return lead


def test_shopify_order_matches_b2b_lead_by_phone(db):
    lead = _create_b2b_lead(db)

    payload = {
        "id": 990011,
        "name": "#PB-990011",
        "total_price": "2450.00",
        "shipping_address": {
            "phone": f"+91 {lead.phone}",
            "first_name": "Aarav",
            "city": "Bengaluru",
        },
        "line_items": [{"title": "Gourmet Duo Box"}],
    }

    asyncio.run(_route_shopify_event("orders/create", payload))

    db.expire_all()
    db.refresh(lead)
    assert lead.status == "PAID_CONVERSION"
    assert lead.ai_retry_after is None

    # Verify cold drafts cancelled
    drafts = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).all()
    assert all(d.status == "CANCELLED_PURCHASE_COMPLETED" for d in drafts)

    wa_rows = db.query(OutboundWhatsApp).filter(OutboundWhatsApp.lead_id == lead.id).all()
    assert all(w.status == "CANCELLED_PURCHASE_COMPLETED" for w in wa_rows)

    # Verify callback cancelled
    callbacks = db.query(ScheduledCallback).filter(ScheduledCallback.lead_id == lead.id).all()
    assert all(c.callback_status == "CALLBACK_CANCELLED" for c in callbacks)

    # Verify WorkflowEvent
    event = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type == "PURCHASE_COMPLETED",
    ).first()
    assert event is not None
    assert event.payload["order_id"] == "#PB-990011"
    assert event.payload["amount_inr"] == 2450.00


def test_shopify_order_matches_b2b_lead_by_email(db):
    lead = _create_b2b_lead(db)

    payload = {
        "id": 990022,
        "name": "#PB-990022",
        "total_price": "1800.00",
        "customer": {
            "email": lead.email,
            "first_name": "Rohan",
        },
        "shipping_address": {
            "phone": "9999999999",  # Different phone, email must match
        },
    }

    asyncio.run(_route_shopify_event("orders/create", payload))

    db.expire_all()
    db.refresh(lead)
    assert lead.status == "PAID_CONVERSION"


def test_shopify_order_d2c_does_not_mutate_unrelated_leads(db):
    lead = _create_b2b_lead(db, phone="9876500033", email="unrelated@example.com")

    payload = {
        "id": 990033,
        "name": "#PB-990033",
        "total_price": "650.00",
        "customer": {"email": "retail_customer@gmail.com"},
        "shipping_address": {"phone": "9111222333"},
    }

    asyncio.run(_route_shopify_event("orders/create", payload))

    db.refresh(lead)
    assert lead.status == "IN_CADENCE"


def test_mark_won_cancels_cold_touches_and_callbacks(db):
    lead = _create_b2b_lead(db, phone="9876500044", email="won_lead@example.com")

    res = mark_won(lead_id=lead.id, order_value_inr=5000.0, db=db)
    assert res["status"] == "ok"
    assert res["order_value_inr"] == 5000.0

    db.refresh(lead)
    assert lead.status in ("ORDER_WON", "PAID_CONVERSION")

    drafts = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).all()
    assert all(d.status == "CANCELLED_PURCHASE_COMPLETED" for d in drafts)

    callbacks = db.query(ScheduledCallback).filter(ScheduledCallback.lead_id == lead.id).all()
    assert all(c.callback_status == "CALLBACK_CANCELLED" for c in callbacks)


def test_manual_b2b_payment_verification_cancels_touches_and_suppresses(db):
    from app.api.endpoints import verify_payment, PaymentVerifiedRequest
    lead = _create_b2b_lead(db)
    u_ref = f"UTR-{uuid.uuid4().hex[:8].upper()}"

    body = PaymentVerifiedRequest(actual_cash=3500.0, payment_reference=u_ref)
    res = verify_payment(lead_id=lead.id, body=body, db=db)
    assert res["status"] == "success"
    assert res["payment_reference"] == u_ref
    assert res["cancelled_drafts"] >= 1

    db.expire_all()
    db.refresh(lead)
    assert lead.status == "PAID_CONVERSION"

    # Verify cold drafts cancelled
    drafts = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).all()
    assert all(d.status == "CANCELLED_PURCHASE_COMPLETED" for d in drafts)

    # Verify callbacks cancelled
    callbacks = db.query(ScheduledCallback).filter(ScheduledCallback.lead_id == lead.id).all()
    assert all(c.callback_status == "CALLBACK_CANCELLED" for c in callbacks)


def test_manual_b2b_payment_rejects_duplicate_utr(db):
    from app.api.endpoints import verify_payment, PaymentVerifiedRequest
    from fastapi import HTTPException
    lead1 = _create_b2b_lead(db)
    lead2 = _create_b2b_lead(db)
    shared_utr = f"UTR-DUP-{uuid.uuid4().hex[:6].upper()}"

    body1 = PaymentVerifiedRequest(actual_cash=2000.0, payment_reference=shared_utr)
    res1 = verify_payment(lead_id=lead1.id, body=body1, db=db)
    assert res1["status"] == "success"

    body2 = PaymentVerifiedRequest(actual_cash=2000.0, payment_reference=shared_utr)
    with pytest.raises(HTTPException) as exc_info:
        verify_payment(lead_id=lead2.id, body=body2, db=db)
    assert exc_info.value.status_code == 400
    assert "already been recorded" in exc_info.value.detail


def test_manual_b2b_payment_rejects_invalid_amount(db):
    from app.api.endpoints import verify_payment, PaymentVerifiedRequest
    from fastapi import HTTPException
    lead = _create_b2b_lead(db)
    body = PaymentVerifiedRequest(actual_cash=0.0, payment_reference="UTR-ZERO-AMT")
    with pytest.raises(HTTPException) as exc_info:
        verify_payment(lead_id=lead.id, body=body, db=db)
    assert exc_info.value.status_code == 422
