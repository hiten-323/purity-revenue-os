from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.models import (
    Base, B2BLead, B2BPayment, AIGrowthTreasurySettings,
    LeadAttributionPath, Product, WorkflowEvent,
)
from app.services.agent_economy import AgentEconomyService


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_payment_ledger_is_reference_idempotent_and_supports_partial_collections():
    db = _db()
    settings = AIGrowthTreasurySettings(
        reinvestment_rate=0.20,
        reinvestment_enabled=False,
        bootstrap_mode=True,
        realised_revenue=100000.0,
        realised_margin=35000.0,
        ai_reinvestment_reserve=5250.0,
    )
    lead = B2BLead(
        company="North Star Cafe",
        status="PAYMENT_VERIFIED",
        purchase_sku="PURITY-BOLD-50",
        proposal_suggested_price=180.0,
        proposal_monthly_kg=50.0,
    )
    product = Product(
        sku="PURITY-BOLD-50",
        product_name="Bold 50g",
        cost_price=110.0,
        selling_price=250.0,  # D2C/list price must not be used as B2B invoice price.
        marketplace="Shopify",
        inventory=100,
    )
    db.add_all([settings, lead, product])
    db.commit()

    # A legacy attribution row is not proof of payment and must not block a
    # new payment carrying its own unique bank/receipt reference.
    db.add(LeadAttributionPath(
        lead_id=lead.id,
        stage="PAYMENT_VERIFIED",
        agent_id="discovery",
        weight=1.0,
        occurred_at=datetime.utcnow(),
    ))
    db.commit()

    first = AgentEconomyService.verify_payment_and_allocate(
        db, lead.id, 500.0, "UTR-ABC123", before_status="ORDER_WON"
    )
    assert first["status"] == "success"
    assert first["cogs_known"] is False
    assert first["realised_margin"] == "PENDING_B2B_INVOICE_COSTING"
    assert db.query(B2BPayment).count() == 1
    assert db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "PAYMENT_VERIFIED"
    ).count() == 1

    duplicate = AgentEconomyService.verify_payment_and_allocate(
        db, lead.id, 500.0, "UTR-ABC123", before_status="PAYMENT_VERIFIED"
    )
    assert duplicate["status"] == "error"
    assert db.query(B2BPayment).count() == 1

    # A different reference is a legitimate second/partial collection.
    second = AgentEconomyService.verify_payment_and_allocate(
        db, lead.id, 200.0, "UTR-DEF456", before_status="PAYMENT_VERIFIED"
    )
    assert second["status"] == "success"
    assert second["prior_payments_for_account"] == 1
    assert db.query(B2BPayment).count() == 2

    treasury = AgentEconomyService.get_treasury(db)
    assert treasury["realised_revenue"] == 700.0
    assert treasury["verified_payment_count"] == 2
    assert treasury["verified_b2b_accounts_count"] == 1
    assert treasury["unverified_legacy_revenue"] == 100000.0
    assert treasury["realised_margin"] == "PENDING_B2B_INVOICE_COSTING"
    assert treasury["ai_reinvestment_reserve"] == "FROZEN_UNVERIFIED_MARGIN"
    assert treasury["available_for_reinvestment"] == 0.0

    agents = AgentEconomyService.get_agents(db)
    assert round(sum(a["realised_revenue"] for a in agents), 2) == 700.0
    assert all(a["realised_margin_status"] == "PENDING_B2B_INVOICE_COSTING" for a in agents)
    db.close()


def test_payment_reference_is_globally_unique_across_b2b_accounts():
    db = _db()
    first_lead = B2BLead(company="First Cafe", status="PAYMENT_VERIFIED")
    second_lead = B2BLead(company="Second Cafe", status="PAYMENT_VERIFIED")
    db.add_all([first_lead, second_lead])
    db.commit()

    first = AgentEconomyService.verify_payment_and_allocate(
        db, first_lead.id, 100.0, "UTR-GLOBAL-01"
    )
    second = AgentEconomyService.verify_payment_and_allocate(
        db, second_lead.id, 100.0, "UTR-GLOBAL-01"
    )
    assert first["status"] == "success"
    assert second["status"] == "error"
    assert db.query(B2BPayment).count() == 1
    db.close()


def test_payment_endpoint_requires_reference_and_rejects_zero_cash():
    from app.api.endpoints import PaymentVerifiedRequest, verify_payment

    with pytest.raises(ValidationError):
        PaymentVerifiedRequest(actual_cash=500.0)

    db = _db()
    with pytest.raises(HTTPException) as exc:
        verify_payment(
            999999,
            PaymentVerifiedRequest(actual_cash=0.0, payment_reference="UTR-0000"),
            db,
        )
    assert exc.value.status_code == 422
    db.close()
