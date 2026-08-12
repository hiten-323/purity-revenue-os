import sys
import os
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.database import Base
from app.models.models import B2BLead, ActionQueue, LeadInteraction, WorkflowEvent
from app.services.revenue_os import find_best_opportunities, get_intent_tier

# In-memory test DB
TEST_DATABASE_URL = "sqlite:///:memory:"

@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionTesting = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

def test_buying_intent_overrides_modelled_value(db):
    """
    TEST: BUYING INTENT OVERRIDES MODELLED VALUE
    Lead A: Estimated Value = ₹2,00,000, Reply = "Please send pricing and margins"
    Lead B: Estimated Value = ₹15,00,000, No reply
    EXPECTED: Lead A ranks above Lead B.
    """
    # Create Lead B (High value, no reply)
    lead_b = B2BLead(
        company="Lead B - High Value Retail",
        city="Ludhiana",
        state="Punjab",
        email="procurement@leadb.com",
        phone="9876543211",
        division="supermarket",
        status="DISCOVERED",
        estimated_value=1500000.0,
        phone_verified=True,
        email_verified=True
    )
    db.add(lead_b)
    db.commit()

    # Create Lead A (Lower value, but Replied)
    lead_a = B2BLead(
        company="Lead A - Low Value Cafe",
        city="Bathinda",
        state="Punjab",
        email="contact@leada.com",
        phone="9876543210",
        division="cafe",
        status="REPLIED",
        estimated_value=200000.0,
        phone_verified=True,
        email_verified=True
    )
    db.add(lead_a)
    db.commit()

    # Record reply event for Lead A to ensure ev_summary.replied is True
    reply_event = WorkflowEvent(
        lead_id=lead_a.id,
        event_type="REPLIED",
        actor="PROSPECT",
        channel="email",
        occurred_at=datetime.utcnow()
    )
    db.add(reply_event)
    db.commit()

    # Run OS Opportunity Search
    res = find_best_opportunities(db, state="Punjab")
    
    # Extract ranked list
    flat_results = []
    for bucket in res["buckets"].values():
        flat_results.extend(bucket)
        
    # Re-sort to see global rank
    from app.services.revenue_os import INTENT_TIERS
    def get_sort_key(item):
        tier = item["intent_tier"]
        tier_idx = INTENT_TIERS.index(tier) if tier in INTENT_TIERS else 10
        val = item["estimated_value"] or 0.0
        return (tier_idx, -val)
        
    flat_results.sort(key=get_sort_key)
    
    # Assert Lead A ranks higher than Lead B
    pos_a = next(i for i, x in enumerate(flat_results) if "Lead A" in x["company"])
    pos_b = next(i for i, x in enumerate(flat_results) if "Lead B" in x["company"])
    
    assert pos_a < pos_b, f"Expected Lead A (pos {pos_a}) to rank above Lead B (pos {pos_b})"
    print("Verification passed: Intent overrides modeled value.")

def test_category_gating_and_validation(db):
    """
    Verify that only supported coffee-buying categories are allowed CRM entry,
    and invalid categories are rejected.
    """
    from app.services.lead_discovery import save_discovered_leads
    
    raw_leads = [
        # Disqualified wrong category
        {
            "company": "Golden Shingar Beauty Parlour",
            "city": "Abohar",
            "address": "Main Bazar, Abohar, Punjab",
            "phone": "9999911111",
            "types": ["beauty_salon"],
            "segment": "distributor"
        },
        # Legitimate cafe category
        {
            "company": "The Brewing House Cafe",
            "city": "Bathinda",
            "address": "GT Road, Bathinda, Punjab",
            "phone": "9999922222",
            "types": ["cafe"],
            "segment": "horeca",
            "maps_rating": 4.5,
            "maps_reviews_count": 120
        }
    ]
    
    res = save_discovered_leads(raw_leads, db, origin_city="Abohar")
    
    assert res["inserted"] == 1, f"Expected 1 insertion, got {res['inserted']}"
    assert res["rejected_not_coffee_buyers"] == 1, "Expected beauty parlour to be rejected at the gate."
