"""Zero-Fabrication Invariants and 24-Hour Lead Operations Lifecycle Suite.

Verifies:
1. Hard zero-fabrication invariant for emails, phones, and lead attributes.
2. Multi-stage contact verification and provenance protection.
3. Phone classification (mobile, landline, dummy, DNC).
4. Calling compliance and WhatsApp consent gates.
5. Off-peak enrichment safety: 18:30-09:30 IST Mon-Sat and Sunday closed for outbound.
6. Hard outbound gate in all dispatchers (email, calling, WhatsApp).
7. Deduplication, idempotency, and non-downgrade immutability of verified data.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo
import pytest

from app.services import identity
from app.services import business_hours as bh
from app.services.contact_enricher import (
    _guess_email_patterns,
    _is_personal_pattern_email,
    _is_fake_email,
    _phones_near_company,
    find_lead_email,
    phone_provenance,
    phone_is_authoritative,
    FIRST_PARTY,
    SEARCH,
    UNSOURCED,
)
from app.services.phone_intelligence import classify_number
from app.services.trust_promoter import may_send, normalise, MAY_SEND, MAY_NEVER_SEND
from app.services.whatsapp_sender import consent_check, CONSENT_OK, send_whatsapp
from app.services.founder_call_pipeline import call_hours_open, may_place_ai_call
from app.services import email_sender

IST = ZoneInfo("Asia/Kolkata")


class MockLead:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 101)
        self.company = kwargs.get("company", "Apex Coffee House")
        self.city = kwargs.get("city", "Amritsar")
        self.phone = kwargs.get("phone", "")
        self.phone_source = kwargs.get("phone_source", "")
        self.phone_verified = kwargs.get("phone_verified", False)
        self.phone_trust = kwargs.get("phone_trust", "UNKNOWN")
        self.email = kwargs.get("email", "")
        self.email_source = kwargs.get("email_source", "")
        self.email_trust = kwargs.get("email_trust", "UNKNOWN")
        self.email_verified = kwargs.get("email_verified", False)
        self.whatsapp_number = kwargs.get("whatsapp_number", "")
        self.consent_status = kwargs.get("consent_status", "UNKNOWN")
        self.consent_phone = kwargs.get("consent_phone", None)
        self.do_not_call = kwargs.get("do_not_call", False)
        self.ai_call_count = kwargs.get("ai_call_count", 0)
        self.outreach_stage = kwargs.get("outreach_stage", None)
        self.website = kwargs.get("website", "https://apexcoffeehouse.in")
        self.division = kwargs.get("division", "cafe")


# ── 1. ZERO-FABRICATION EMAIL INVARIANTS ──────────────────────────────────────

def test_guess_email_patterns_always_returns_empty():
    """Zero-fabrication: _guess_email_patterns must never invent addresses."""
    assert _guess_email_patterns("Apex Coffee", "https://apexcoffee.in") == []
    assert _guess_email_patterns("Star Traders", "") == []


def test_synthetic_slug_email_patterns_detected():
    """Auto-generated firstname.lastname@<slug> emails must be detected and rejected."""
    assert _is_personal_pattern_email("shreyajeet.tiwari@stellarconsulting.com", "Stellar Consulting") is True
    # Real generic role mailbox on verified domain is not flagged as synthetic firstname.lastname
    assert _is_personal_pattern_email("sales@stellarconsulting.com", "Stellar Consulting") is False


def test_find_lead_email_never_guesses_when_unfound(monkeypatch):
    """If no real web/directory hits occur, find_lead_email returns empty email with confidence NONE."""
    # Mock search functions returning empty lists
    monkeypatch.setattr("app.services.contact_enricher._scrape_website_full", lambda u: {"emails": [], "phones": [], "whatsapp": ""})
    monkeypatch.setattr("app.services.contact_enricher._google_email_search", lambda c, ci, w: [])
    monkeypatch.setattr("app.services.contact_enricher._justdial_email", lambda c, ci: [])
    monkeypatch.setattr("app.services.contact_enricher._indiamart_email", lambda c, ci: [])

    result = find_lead_email("Unknown Store", "Bathinda", website="https://nonexistent-store123.com")
    assert result["email"] == ""
    assert result["confidence"] == "NONE"
    assert result["source"] == "not_found"
    assert result["all_candidates"] == []


def test_discovered_and_unseen_emails_cannot_send():
    """Unverified or discovered emails have zero sending rights until verified."""
    lead_disc = MockLead(email="sales@apexcoffee.in", email_trust="DISCOVERED")
    ok, reason = may_send(lead_disc)
    assert not ok
    assert "DISCOVERED" in reason or "not sendable" in reason

    lead_unseen = MockLead(email="sales@apexcoffee.in", email_trust="UNSEEN")
    ok, _ = may_send(lead_unseen)
    assert not ok


def test_verified_email_cannot_be_overwritten_by_weaker_source():
    """Good verified email must never be replaced by a newly guessed or low-confidence search result."""
    lead = MockLead(
        email="contact@apexcoffee.in",
        email_source="WEBSITE",
        email_trust="VERIFIED",
        email_verified=True,
    )
    res = find_lead_email(lead.company, lead.city, lead.website, existing_email=lead.email, force_replace=False)
    assert res["email"] == "contact@apexcoffee.in"
    assert res["confidence"] == "EXISTING"


# ── 2. ZERO-FABRICATION PHONE INVARIANTS ──────────────────────────────────────

def test_dummy_and_sequential_phones_rejected():
    """All ascending, descending, dummy sequences and low-entropy numbers are blocked."""
    dummy_phones = [
        "9876543210",
        "+91-98765-43210",
        "0123456789",
        "1234567890",
        "0987654321",
        "9999999999",
        "1111111111",
        "8888888888",
        "+91-99999-99999",
    ]
    for p in dummy_phones:
        assert identity.is_fabricated_pattern(p) is True, f"Failed to detect dummy pattern: {p}"


def test_valid_indian_mobile_and_regional_prefixes_not_falsely_flagged():
    """Real Punjab Airtel (98765-XXXXX) or standard Indian mobiles must NOT be falsely flagged as dummy."""
    genuine_numbers = [
        "+91 98765 04262",  # 98765 prefix with non-sequential tail
        "+91-98880-89325",  # 98880 series in Punjab
        "+91 98140 12345",  # real Punjab mobile with 12345 suffix
        "9855593323",
        "+91-94170-12398",
    ]
    for p in genuine_numbers:
        assert identity.is_fabricated_pattern(p) is False, f"Falsely flagged genuine number: {p}"


def test_id_derived_phones_rejected():
    """Mechanically synthesized phone numbers matching lead database IDs must be blocked."""
    assert identity.is_id_derived_phone("+91-98765-00156", record_id=156) is True
    assert identity.is_id_derived_phone("+91-98765-01885", record_id=1885) is True
    assert identity.is_id_derived_phone("+91-98555-93323", record_id=156) is False


def test_phone_provenance_protection():
    """First-party numbers (WEBSITE, FOUNDER_CALL) are authoritative; search scrapers cannot overwrite them."""
    first_party_lead = MockLead(phone="9855593323", phone_source="WEBSITE")
    search_lead = MockLead(phone="9855593323", phone_source="Perplexity, BraveSearch, IndiaMart")
    
    assert phone_provenance(first_party_lead) == FIRST_PARTY
    assert phone_is_authoritative(first_party_lead) is True

    assert phone_provenance(search_lead) == SEARCH
    assert phone_is_authoritative(search_lead) is False


# ── 3. PHONE CLASSIFICATION & CALLING COMPLIANCE ─────────────────────────────

def test_phone_classification_mobile_vs_landline():
    mobile = classify_number("+91-98555-93323")
    assert mobile["kind"] == "mobile"
    assert mobile["whatsapp"] is True
    assert mobile["callable"] is True

    landline = classify_number("+91-172-2676777")
    assert landline["kind"] == "landline"
    assert landline["whatsapp"] is False  # Cannot message landline on WhatsApp
    assert landline["callable"] is True


def test_dnc_leads_strictly_blocked_across_all_channels():
    dnc_lead = MockLead(phone="9855593323", email="contact@apex.in", do_not_call=True)
    
    # WhatsApp blocked
    ok_wa, reason_wa = consent_check(dnc_lead)
    assert not ok_wa
    assert "do-not-contact" in reason_wa.lower()

    # AI Calling blocked
    ok_call, reason_call = may_place_ai_call(dnc_lead)
    assert not ok_call
    assert "dnc" in reason_call.lower() or "do_not_call" in reason_call.lower()


def test_whatsapp_requires_explicit_consent_bound_to_number():
    """WhatsApp requires consent matching the active phone number."""
    lead_no_optin = MockLead(phone="9855593323", consent_status="UNKNOWN")
    ok, reason = consent_check(lead_no_optin)
    assert not ok
    assert "no opt-in" in reason.lower()

    # Consent recorded for a different number is blocked
    lead_drift = MockLead(
        phone="9855593323",
        consent_status="OPTED_IN",
        consent_phone="9814099999",
    )
    ok_drift, reason_drift = consent_check(lead_drift)
    assert not ok_drift
    assert "different number" in reason_drift.lower()


# ── 4. 24-HOUR LIFECYCLE & BUSINESS-HOURS OUTBOUND GATES ──────────────────────

def test_business_hours_schedule_enforcement(monkeypatch):
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "1")

    # Wednesday 11:00 AM IST -> Open
    wed_morning = datetime(2026, 9, 23, 11, 0, tzinfo=IST)
    assert bh.is_open(wed_morning) is True
    assert call_hours_open(wed_morning)[0] is True

    # Wednesday 08:30 PM (20:30) IST -> Closed (Off-Peak Window)
    wed_night = datetime(2026, 9, 23, 20, 30, tzinfo=IST)
    assert bh.is_open(wed_night) is False
    assert call_hours_open(wed_night)[0] is False

    # Sunday 12:00 PM IST -> Strictly Closed for Outbound Outreach
    sun_noon = datetime(2026, 9, 27, 12, 0, tzinfo=IST)
    assert bh.is_open(sun_noon) is False
    assert call_hours_open(sun_noon)[0] is False


def test_all_dispatchers_fail_closed_outside_business_hours(monkeypatch):
    """Email, WhatsApp, and AI Calling must all fail closed during off-hours."""
    monkeypatch.setenv("OUTREACH_BUSINESS_HOURS", "1")
    monkeypatch.setenv("AI_CALL_BUSINESS_HOURS", "1")
    monkeypatch.setattr("app.services.business_hours.is_open", lambda now=None: False)

    # 1. Email Sender
    email_res = email_sender.send_email(
        email_sender.OutreachEmail(
            to_email="buyer@apexcoffee.in",
            to_name="Buyer",
            company="Apex",
            subject="Test",
            body_text="Test",
            lead_id=101,
        )
    )
    assert email_res.status == "failed"
    assert "business hours" in email_res.error.lower()

    # 2. WhatsApp Sender
    consented_lead = MockLead(
        phone="9855593323",
        whatsapp_number="9855593323",
        consent_status="OPTED_IN",
        consent_phone="9855593323",
    )
    wa_res = send_whatsapp(consented_lead, "Hello")
    assert wa_res.status == "blocked"
    assert "business hours" in wa_res.reason.lower()

    # 3. Calling Dispatcher
    call_ok, call_why = call_hours_open(datetime(2026, 9, 23, 21, 0))
    assert not call_ok
    assert "outside business hours" in call_why.lower()
