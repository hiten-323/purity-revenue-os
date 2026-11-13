"""
Unit and integration tests for Enrichment Engine (High-Intent Conversion V1.0).
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

from app.services.enrichment_engine import (
    _clean_phone,
    _clean_email,
    _pick_decision_maker_email,
    _crawl_website,
    enrich_lead,
    enrich_batch,
    HARD_BLOCK,
)
from app.models.models import Base, B2BLead, LeadEvidence, WorkflowEvent
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_clean_phone_valid():
    assert _clean_phone("+91-98765-43210") == "+91-98765-43210"
    assert _clean_phone("9876543210") == "+91-98765-43210"
    assert _clean_phone("09876543210") == "+91-98765-43210"
    assert _clean_phone("+91 87654 32109") == "+91-87654-32109"
    assert _clean_phone("7087870879") == "+91-70878-70879"


def test_clean_phone_invalid():
    # Landlines
    assert _clean_phone("0172-2271499") is None
    assert _clean_phone("011-23456789") is None
    # Toll-free
    assert _clean_phone("18001802468") is None
    assert _clean_phone("18602660333") is None
    # Fake / Junk / Short
    assert _clean_phone("12345") is None
    assert _clean_phone("0000000000") is None
    assert _clean_phone("9999999999") is None
    assert _clean_phone("1234567890") is None


def test_clean_phone_hard_block():
    assert "8591977190" in HARD_BLOCK
    assert _clean_phone("8591977190") is None
    assert _clean_phone("+91-85919-77190") is None
    assert _clean_phone("+918591977190") is None


def test_clean_email():
    assert _clean_email("procurement@grandhotel.in") == "procurement@grandhotel.in"
    assert _clean_email("info@punjabdistributors.com") == "info@punjabdistributors.com"

    # Aggregator / bad portal domains rejected
    assert _clean_email("test@justdial.com") is None
    assert _clean_email("vendor@indiamart.com") is None
    assert _clean_email("support@wix.com") is None
    assert _clean_email("noreply@example.com") is None

    # Foreign TLDs rejected for Indian B2B lead
    assert _clean_email("sales@coffeeroaster.com.au") is None
    assert _clean_email("contact@supplier.co.uk") is None


def test_decision_maker_email_selection():
    emails = [
        "info@roastery.in",
        "procurement@roastery.in",
        "careers@roastery.in",
    ]
    assert _pick_decision_maker_email(emails) == "procurement@roastery.in"

    fallback_emails = ["info@cafe.in", "contact@cafe.in"]
    assert _pick_decision_maker_email(fallback_emails) in ("info@cafe.in", "contact@cafe.in")


def test_crawl_and_signal_detection():
    sample_html = """
    <html>
      <head><title>The Royal Cafe & Roastery</title></head>
      <body>
        <h1>Welcome to The Royal Cafe</h1>
        <p>We are looking for instant coffee bulk orders and soluble coffee supply for our hotel chain.</p>
        <p>Currently our banquet uses Nescafe Classic and Bru Gold, but we are open to vendor switching.</p>
        <p>Our premises include an executive office pantry and staff canteen serving 120 employees.</p>
        <p>We provide complimentary breakfast to all morning guests.</p>
        <p>For procurement, contact Mr. Rajesh Sharma, Purchase Manager.</p>
        <p>Email: purchase@royalcafe.in or info@royalcafe.in</p>
        <p>Phone: +91 98765 11223</p>
        <p>WhatsApp: <a href="https://wa.me/919876511223">Chat with us</a></p>
        <footer>
          FSSAI Lic No: 12345678901234
          GSTIN: 07AAAAA0000A1Z5
        </footer>
      </body>
    </html>
    """

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html
        mock_get.return_value = mock_resp

        result = _crawl_website("https://royalcafe.in", "The Royal Cafe")

        assert "+91-98765-11223" in result["phones"]
        assert "purchase@royalcafe.in" in result["emails"]
        assert result["whatsapp"] == "+91-98765-11223"
        assert result["decision_maker_name"] == "Mr. Rajesh Sharma"
        assert result["decision_maker_title"] == "Purchase Manager"
        assert result["fssai"] == "12345678901234"
        assert result["gstin"] == "07AAAAA0000A1Z5"

        assert result["coffee_signals"]["instant_coffee"] is True
        assert result["coffee_signals"]["pantry_cafeteria"] is True
        assert result["coffee_signals"]["breakfast_buffet"] is True
        assert result["coffee_signals"]["wholesale_procurement"] is True

        assert "Nescafe" in result["detected_brands"] or "Nestle Classic" in result["detected_brands"]
        assert "Bru Instant" in result["detected_brands"]


def test_zero_fabrication_on_empty_content():
    empty_html = "<html><body><h1>Under Construction</h1></body></html>"
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = empty_html
        mock_get.return_value = mock_resp

        result = _crawl_website("https://emptycafe.in", "Empty Cafe")
        assert result["emails"] == []
        assert result["phones"] == []
        assert result["decision_maker_name"] is None
        assert result["detected_brands"] == []
        assert not any(result["coffee_signals"].values())


def test_enrich_lead_syncs_with_db_and_high_intent():
    db = _db()

    lead = B2BLead(
        company="Punjab Grand Hotel",
        city="Ludhiana",
        division="hotel",
        segment="hotel",
        status="DISCOVERED",
        website="https://punjabgrand.in",
        phone=None,
        email=None,
        coffee_buying_score=0,
        intent_score=0,
    )
    db.add(lead)
    db.commit()

    sample_html = """
    <html>
      <body>
        <p>Punjab Grand Hotel offers bulk coffee supply and corporate pantry services.</p>
        <p>Serving instant coffee in all 80 rooms.</p>
        <p>Current coffee supplier is Nescafe Sunrise.</p>
        <p>Contact Purchase Head Mr. Vikram Singh at purchase@punjabgrand.in or call +91-98150-12345.</p>
      </body>
    </html>
    """

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = sample_html
        mock_get.return_value = mock_resp

        res = enrich_lead(
            company=lead.company,
            city=lead.city,
            website=lead.website,
            division=lead.division,
            lead=lead,
            db=db,
        )

        db.commit()

        # Contacts updated
        assert lead.email == "purchase@punjabgrand.in"
        assert lead.phone == "+91-98150-12345"
        assert lead.contact_name == "Mr. Vikram Singh"
        assert lead.contact_title == "Purchase Head"
        assert lead.current_brand in ("Nescafe", "Nestle Sunrise")
        assert lead.current_supplier in ("Nescafe", "Nestle Sunrise")

        # Truth Layer evidence created
        evs = db.query(LeadEvidence).filter(LeadEvidence.lead_id == lead.id).all()
        assert len(evs) > 0

        # Intent score elevated
        assert lead.intent_score >= 80
        assert lead.intent_tier == "Priority A"
        assert lead.intelligence_status == "ENRICHED"
        assert "instant coffee" in lead.qualification_notes.lower()
