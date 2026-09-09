from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy import event
from app.database.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    sku = Column(String, unique=True, index=True)
    product_name = Column(String)
    cost_price = Column(Float)
    selling_price = Column(Float)
    marketplace = Column(String)
    inventory = Column(Integer)

class Marketplace(Base):
    __tablename__ = "marketplaces"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    api_key = Column(String)

class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    marketplace = Column(String)
    quantity = Column(Integer)
    revenue = Column(Float)
    sale_date = Column(DateTime, default=datetime.utcnow)

class InventoryRecord(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    stock_level = Column(Integer)
    marketplace = Column(String)
    last_updated = Column(DateTime, default=datetime.utcnow)

class Competitor(Base):
    __tablename__ = "competitors"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    product_id = Column(Integer, ForeignKey("products.id"))
    price = Column(Float)
    marketplace = Column(String)
    last_checked = Column(DateTime, default=datetime.utcnow)

class Recommendation(Base):
    __tablename__ = "recommendations"
    id = Column(Integer, primary_key=True, index=True)
    agent = Column(String)
    action_type = Column(String)
    details = Column(JSON)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class AgentLog(Base):
    __tablename__ = "agent_logs"
    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String)
    action = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    payload = Column(JSON)

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    channel = Column(String) # WhatsApp, Email
    message = Column(String)
    sent_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String)

class B2BLead(Base):
    __tablename__ = "b2b_leads"
    id = Column(Integer, primary_key=True, index=True)
    company = Column(String, unique=True, index=True)
    contact_name = Column(String)
    contact_title = Column(String)
    email = Column(String)
    phone = Column(String)
    city = Column(String)
    division = Column(String) # distributor, corporate, retail, gifting, horeca
    lead_source = Column(String, default="Google Maps") # Google Maps, IndiaMART, LinkedIn, TradeIndia, Tender, Referral
    industry = Column(String, default="Corporate") # IT Companies, Hospitals, Manufacturing, Hospitality, Wholesale, Corporate
    region = Column(String, default="South") # North, South, East, West
    company_size = Column(String, default="Mid-Market") # Enterprise, Mid-Market, SMB
    contact_persona = Column(String, default=None)   # never default a job title nobody told us # Admin Manager, Procurement Manager, HR Manager, Office Manager, Facility Manager, Owner
    lost_reason = Column(String, nullable=True)
    estimated_value = Column(Float, default=0.0)
    score = Column(Integer, default=0)
    intent_score = Column(Integer, default=0)
    intent_tier = Column(String, default="Cold")
    probability = Column(Float, default=0.0)
    status = Column(String, default="DISCOVERED") # DISCOVERED, QUALIFIED, EMAIL_SENT, REPLIED, MEETING_BOOKED, MEETING_COMPLETED, SAMPLE_SENT, PROPOSAL_SENT, ORDER_WON, ONBOARDED, REORDER_PREDICTED, UPSELL_OFFERED, ACCOUNT_GROWTH, COLD
    priority = Column(String, default="MEDIUM") # HIGH, MEDIUM, LOW
    recommended_action = Column(String, nullable=True)
    qualification_notes = Column(String, nullable=True)
    
    # Sampling Feedback scores (1-10 scale)
    sample_taste = Column(Integer, nullable=True)
    sample_aroma = Column(Integer, nullable=True)
    sample_packaging = Column(Integer, nullable=True)
    sample_intent = Column(Integer, nullable=True)
    sample_purchase_again = Column(Integer, nullable=True)
    expected_monthly_consumption_kg = Column(Float, default=0.0)
    
    # Proposal Details
    proposal_monthly_kg = Column(Float, default=0.0)
    proposal_suggested_price = Column(Float, default=0.0)
    proposal_suggested_margin = Column(Float, default=0.0)
    proposal_discount_percent = Column(Float, default=0.0)
    proposal_recommended_margin = Column(Float, default=0.0)
    proposal_text = Column(String, nullable=True)
    
    # Enrichment & SKU tracking fields
    website = Column(String, nullable=True)
    address = Column(String, nullable=True)
    whatsapp_number = Column(String, nullable=True)
    # V1.1 contact provenance: a phone/WhatsApp is only "real" once confirmed
    # by web-wide enrichment (directories + search engines)
    phone_verified = Column(Boolean, default=False)
    phone_source = Column(String, nullable=True)        # e.g. "JustDial, WebSearch"
    contact_searched_at = Column(DateTime, nullable=True)
    linkedin = Column(String, nullable=True)
    decision_maker_score = Column(Integer, default=5)
    sample_sku = Column(String, default="Purica")
    purchase_sku = Column(String, nullable=True)
    reorder_sku = Column(String, nullable=True)
    
    # Email Outreach Sequence & Analytics Tracking
    email_sequence_stage = Column(Integer, default=0) # 0: None, 1: Day 0, 2: Day 3, 3: Day 7, 4: Day 14
    email_sequence_last_sent = Column(DateTime, nullable=True)
    email_opens = Column(Integer, default=0)
    email_clicks = Column(Integer, default=0)

    # Revenue Engine fields (added via migration on first use)
    final_score = Column(Integer, default=0, nullable=True)
    revenue_tier = Column(String, default="Bronze", nullable=True)
    estimated_annual_value = Column(Float, default=0.0, nullable=True)
    segment = Column(String, default="corporate", nullable=True)

    # Real size signals from Google Places (verified, used for revenue estimation)
    maps_rating = Column(Float, nullable=True)          # 0–5 quality signal
    maps_reviews_count = Column(Integer, nullable=True) # footfall/size proxy

    # New Phase 1 & 2 Operating System fields
    stage_entered_date = Column(DateTime, default=datetime.utcnow, nullable=True)
    gifting_category = Column(String, nullable=True)
    distribution_outlets = Column(Integer, default=0, nullable=True)
    gifting_days_until_event = Column(Integer, nullable=True)

    # V1.0-RC1 Calling OS fields
    call_status = Column(String, nullable=True)
    call_quality_score = Column(Integer, default=0, nullable=True)
    call_summary = Column(String, nullable=True)
    call_transcript = Column(String, nullable=True)
    call_recording_url = Column(String, nullable=True)
    vapi_call_id = Column(String, nullable=True)
    human_answered = Column(Boolean, default=False, nullable=True)
    call_duration_seconds = Column(Integer, default=0, nullable=True)
    call_attempts = Column(Integer, default=0, nullable=True)
    last_call_date = Column(DateTime, nullable=True)
    do_not_call = Column(Boolean, default=False, nullable=True)
    dnc_reason = Column(String, nullable=True)
    dnc_override_by = Column(String, nullable=True)
    dnc_override_reason = Column(String, nullable=True)
    call_cost = Column(Float, default=0.0, nullable=True)
    call_provider = Column(String, nullable=True)
    call_minutes = Column(Float, default=0.0, nullable=True)
    current_brand = Column(String, nullable=True)
    current_supplier = Column(String, nullable=True)
    price_per_kg = Column(Float, nullable=True)
    competitor_strength = Column(Integer, default=0, nullable=True)
    monthly_consumption = Column(String, nullable=True)
    decision_maker = Column(String, nullable=True)
    sample_requested = Column(Boolean, default=False, nullable=True)
    meeting_requested = Column(Boolean, default=False, nullable=True)
    budget_range = Column(String, nullable=True)
    objection_reason = Column(String, nullable=True)
    next_followup_date = Column(String, nullable=True)
    call_estimated_value = Column(Float, default=0.0, nullable=True)
    blended_realization_per_kg = Column(Float, default=1400.0, nullable=True)
    lead_tier = Column(String, nullable=True)
    company_normalized = Column(String, nullable=True)
    lead_owner = Column(String, default="Hiten", nullable=True)
    lead_locked_until = Column(DateTime, nullable=True)
    acquisition_source = Column(String, nullable=True)
    consent_status = Column(String, default="UNKNOWN", nullable=True)
    consent_source = Column(String, nullable=True)
    consent_timestamp = Column(DateTime, nullable=True)

    # AI-qualification -> founder-call pipeline (founder_call_pipeline.py).
    # Deliberately NOT folded into consent_status: whatsapp_sender.CONSENT_OK,
    # smart_outreach and CallingAgentService all read that field, so a stage
    # written there would become a sending permission on another channel.
    # "An AI rang this shop once" must never mean "this number may be
    # WhatsApped". These columns are read by the pipeline and by reporting,
    # and by nothing that sends.
    outreach_stage = Column(String, nullable=True)          # see pipeline.STAGES
    outreach_stage_at = Column(DateTime, nullable=True)
    ai_call_count = Column(Integer, default=0, nullable=True)
    ai_interest_level = Column(String, nullable=True)       # HOT | WARM | COOL
    founder_callback_window = Column(String, nullable=True)  # what they asked for
    lead_temperature_score = Column(Float, default=0.0, nullable=True)
    lead_temperature_tier = Column(String, nullable=True)

    # V1.1 & V1.2 upgrades
    reality_score = Column(Float, default=0.0, nullable=True)
    reality_grade = Column(String, default="COLD", nullable=True)
    freight_cost_estimate = Column(Float, default=0.0, nullable=True)
    discount_given = Column(Float, default=0.0, nullable=True)
    sampling_cost_total = Column(Float, default=0.0, nullable=True)
    credit_period_days = Column(Integer, default=30, nullable=True)
    actual_margin_net = Column(Float, default=0.0, nullable=True)
    sample_followup_status = Column(String, default="GREEN", nullable=True)
    days_to_cash = Column(Integer, default=0, nullable=True)
    deal_health = Column(String, default="GREEN", nullable=True)
    call_outcome_last = Column(String, nullable=True)
    velocity_days_reply = Column(Integer, default=0, nullable=True)

    # Email Verification & Approval (v2.0)
    # What the business actually is, per its Google Maps listing — distinct
    # from `division`, which records the segment we were searching when we
    # found it. Conflating the two filed cafes as distributors.
    maps_types = Column(String, nullable=True)
    coffee_buying_score = Column(Integer, default=0, nullable=True)

    # ── Contact trust & provenance ──────────────────────────────────────────
    # This application is not the only writer to this database. 82 addresses
    # were found written straight into SQLite with email_verified=1 and no
    # event. So a contact does not become actionable by existing: it carries a
    # trust level and a provenance, and outreach reads the trust level.
    #
    #   UNKNOWN          nothing established
    #   DISCOVERED       observed from a public source — NOT sendable
    #   VERIFIED         passed the verification workflow
    #   FOUNDER_VERIFIED the buyer gave it to the founder directly
    #   REPLIED          they actually replied from it — the strongest proof
    #   UNTRUSTED        present with no provenance; assume out-of-band
    #   PURGED           rejected by integrity rules; never re-guessed
    email_trust = Column(String, default="UNKNOWN", nullable=True)
    email_source = Column(String, nullable=True)          # GOOGLE_MAPS, WEBSITE, FOUNDER_CALL…
    email_collected_at = Column(DateTime, nullable=True)
    email_verified_at = Column(DateTime, nullable=True)
    phone_trust = Column(String, default="UNKNOWN", nullable=True)
    phone_collected_at = Column(DateTime, nullable=True)
    phone_verified_at = Column(DateTime, nullable=True)
    # Fingerprint of the contact fields as the application last left them.
    # A mismatch at boot means someone else changed the row — that is the only
    # way to detect a write that left no event.
    contact_fingerprint = Column(String, nullable=True)

    # ── Actionability, kept SEPARATE from trust ─────────────────────────────
    # Trust answers "is this contact real?". Status answers "should we contact
    # them today?". They are independent: an address can be REPLIED — the
    # strongest possible proof it is real — and OPTED_OUT at the same time.
    # Collapsing the two would make an opt-out look like a data-quality problem.
    #   CONTACTABLE | FOLLOW_UP | PAUSED | DO_NOT_CONTACT | OPTED_OUT
    #   BOUNCED | ARCHIVED
    contact_status = Column(String, default="CONTACTABLE", nullable=True)
    contact_status_reason = Column(String, nullable=True)
    # Does the BUSINESS exist, independent of whether we can reach it? A real
    # distributor stays real when their sales rep leaves and the email dies.
    #   UNVERIFIED | VERIFIED_EXISTING | PERMANENTLY_CLOSED | NOT_A_BUSINESS
    business_trust = Column(String, default="UNVERIFIED", nullable=True)
    # 0-100 from accumulated evidence. Ranks contacts without replacing the
    # lifecycle state — a score is for sorting, a state is for permission.
    contact_confidence = Column(Integer, default=0, nullable=True)

    coffee_buying_evidence = Column(String, nullable=True)
    # Google Places identity + amenities. place_id is the key that makes a
    # Details lookup possible; serves_breakfast is the amenity Google actually
    # reports, as opposed to "it is a hotel so it probably serves breakfast".
    place_id = Column(String, nullable=True, index=True)
    serves_breakfast = Column(Boolean, nullable=True)
    place_details_checked_at = Column(DateTime, nullable=True)
    # OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY, per Google Places.
    # Feeds the PERMANENTLY_CLOSED disqualifier.
    business_status = Column(String, nullable=True)
    # Set only when Truth Layer evidence collection has run for this lead.
    # NULL means genuinely unassessed — distinct from a classification of COLD
    # (assessed, nothing notable) or REJECT (assessed, disqualified).
    evidence_collected_at = Column(DateTime, nullable=True)
    # Explicit evaluation lifecycle. Separate axis from classification:
    #   NOT_STARTED  never touched by the intelligence pipeline
    #   COLLECTING   evidence collection in progress
    #   SCORING      evidence gathered, assessment running
    #   COMPLETE     assessed — classification is now authoritative
    #   FAILED       pipeline errored; classification must not be trusted
    intelligence_status = Column(String, default="NOT_STARTED", nullable=True)
    # Provenance for `division`, which is DERIVED, not observed. Without this a
    # seed default was indistinguishable from a Google-Maps-confirmed category.
    #   "Google Maps Types"      confidence 0.95, verified True
    #   "Business Name Heuristic" confidence 0.60, verified False
    #   "Search Segment Default"  confidence 0.25, verified False
    division_source = Column(String, nullable=True)
    division_confidence = Column(Float, default=0.25, nullable=True)
    division_verified = Column(Boolean, default=False, nullable=True)

    email_verified = Column(Boolean, default=False, nullable=True)
    email_confidence = Column(Integer, default=0, nullable=True)
    email_verification_status = Column(String, default="UNVERIFIED", nullable=True)
    email_mx_valid = Column(Boolean, default=False, nullable=True)
    email_is_generic = Column(Boolean, default=False, nullable=True)
    email_domain_match = Column(Boolean, default=False, nullable=True)
    email_verify_reason = Column(String, nullable=True)
    email_approved_by_founder = Column(Boolean, default=False, nullable=True)
    email_rejected_by_founder = Column(Boolean, default=False, nullable=True)

    state = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    distance_from_origin = Column(Float, nullable=True)
    origin_city = Column(String, nullable=True)
    searched_category = Column(String, nullable=True)

    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailDraft(Base):
    __tablename__ = "email_drafts"
    id            = Column(Integer, primary_key=True, index=True)
    lead_id       = Column(Integer, ForeignKey("b2b_leads.id"))
    follow_up_type = Column(String)   # nudge | sample_push | post_sample | meeting_book | re_engage
    subject       = Column(String)
    body          = Column(String)
    reason        = Column(String)    # why this email was drafted
    status        = Column(String, default="DRAFT")  # DRAFT | FOUNDER_APPROVED | QUEUED | SENDING | SENT | FAILED | SKIPPED | EDITED
    created_at    = Column(DateTime, default=datetime.utcnow)
    sent_at       = Column(DateTime, nullable=True)
    lead          = relationship("B2BLead", foreign_keys=[lead_id])

    # V1.1 Production Refinements - State Machine & Zoho Sync
    opportunity_id = Column(Integer, ForeignKey("revenue_opportunities.id"), nullable=True)
    recipient     = Column(String, nullable=True)
    campaign      = Column(String, nullable=True)
    workflow_id   = Column(String, nullable=True)
    
    draft_created_at = Column(DateTime, default=datetime.utcnow)
    approved_at   = Column(DateTime, nullable=True)
    queued_at     = Column(DateTime, nullable=True)
    sending_at    = Column(DateTime, nullable=True)
    failed_at     = Column(DateTime, nullable=True)
    
    zoho_message_id = Column(String, nullable=True, index=True)
    smtp_response = Column(String, nullable=True)
    
    delivery_status = Column(String, default="UNKNOWN") # DELIVERED | BOUNCED | UNKNOWN
    open_status   = Column(Boolean, default=False)
    opened_at     = Column(DateTime, nullable=True)
    reply_status  = Column(Boolean, default=False)
    replied_at    = Column(DateTime, nullable=True)
    
    retry_count   = Column(Integer, default=0)
    approval_request_id = Column(Integer, ForeignKey("approval_requests.id"), nullable=True)


class CallHistory(Base):
    __tablename__ = "call_history"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"))
    call_date = Column(DateTime, default=datetime.utcnow)
    duration = Column(Integer, default=0) # seconds
    cost = Column(Float, default=0.0)
    status = Column(String)
    quality_score = Column(Integer, default=0)
    recording_url = Column(String, nullable=True)
    summary = Column(String, nullable=True)

    # V1.1 Production Refinements - AI Call Lifecycle
    vapi_call_sid   = Column(String, nullable=True)
    call_status     = Column(String, default="DRAFT")  # DRAFT | APPROVED | QUEUED | SENDING | COMPLETED | FAILED
    call_transcript = Column(String, nullable=True)
    call_duration   = Column(Integer, default=0)
    approval_request_id = Column(Integer, ForeignKey("approval_requests.id"), nullable=True)
    
    lead = relationship("B2BLead", foreign_keys=[lead_id])


class OutboundWhatsApp(Base):
    __tablename__ = "outbound_whatsapp"
    id            = Column(Integer, primary_key=True, index=True)
    lead_id       = Column(Integer, ForeignKey("b2b_leads.id"))
    opportunity_id = Column(Integer, ForeignKey("revenue_opportunities.id"), nullable=True)
    recipient     = Column(String, nullable=True)
    message       = Column(String, nullable=True)
    whatsapp_message_id = Column(String, nullable=True, index=True)
    status        = Column(String, default="DRAFT")  # DRAFT | APPROVED | QUEUED | SENDING | SENT | DELIVERED | READ | FAILED
    whatsapp_response = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)
    approved_at   = Column(DateTime, nullable=True)
    queued_at     = Column(DateTime, nullable=True)
    sending_at    = Column(DateTime, nullable=True)
    sent_at       = Column(DateTime, nullable=True)
    delivered_at  = Column(DateTime, nullable=True)
    read_at       = Column(DateTime, nullable=True)
    failed_at     = Column(DateTime, nullable=True)
    retry_count   = Column(Integer, default=0)
    approval_request_id = Column(Integer, ForeignKey("approval_requests.id"), nullable=True)

    lead          = relationship("B2BLead", foreign_keys=[lead_id])


class ActionQueue(Base):
    __tablename__ = "action_queue"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"))
    action_type = Column(String) # CALL_LEAD, FOLLOWUP_SAMPLE, SEND_PROPOSAL, DISPATCH_SAMPLE, REORDER_ALERT
    priority_score = Column(Float, default=0.0)
    expected_margin = Column(Float, default=0.0)
    due_date = Column(DateTime, nullable=True)
    status = Column(String, default="PENDING") # PENDING, COMPLETED, DISMISSED
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class RevenueForecastSnapshot(Base):
    __tablename__ = "revenue_forecast_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    snapshot_date = Column(DateTime, default=datetime.utcnow)
    expected_revenue_7d = Column(Float, default=0.0)
    expected_margin_7d = Column(Float, default=0.0)
    actual_revenue_7d = Column(Float, default=0.0)
    actual_margin_7d = Column(Float, default=0.0)
    forecast_accuracy_pct = Column(Float, default=100.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class LearnedPattern(Base):
    """
    A pattern the system has LEARNED from real outcomes — never a guess.

    Each row is one measured fact, e.g. "segment=distributor reply_rate=12.5%
    over 40 touches". sample_size is stored with every pattern so the UI can
    refuse to act on thin evidence instead of presenting noise as insight.

    Lives in the DB (not browser state), so the dashboard shows the same learned
    memory whenever and wherever it is opened.
    """
    __tablename__ = "learned_patterns"
    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String, index=True)      # segment | city | channel | category
    key = Column(String, index=True)        # e.g. "distributor", "Abohar", "email"
    metric = Column(String, index=True)     # reply_rate_pct | win_rate_pct | margin_per_touch_rs
    value = Column(Float, default=0.0)
    sample_size = Column(Integer, default=0)   # touches this was measured over
    wins = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ObjectionLearning(Base):
    __tablename__ = "objection_learnings"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"))
    objection_type = Column(String) # Price, MOQ, Delivery, Taste, Existing Supplier
    competitor_name = Column(String, nullable=True)
    offered_price_per_kg = Column(Float, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(String, nullable=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class LeadTimelineEvent(Base):
    __tablename__ = "lead_timeline_events"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"))
    event_type = Column(String) # CALL, EMAIL, SAMPLE, FEEDBACK, PROPOSAL, ORDER
    event_date = Column(DateTime, default=datetime.utcnow)
    title = Column(String)
    description = Column(String, nullable=True)
    meta_json = Column(JSON, nullable=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class GovTender(Base):
    """Active government tenders found via GeM / CPPP / state portals."""
    __tablename__ = "gov_tenders"
    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(String, unique=True, index=True)   # portal reference number
    title = Column(String)
    department = Column(String)
    portal = Column(String, default="GeM")   # GeM, CPPP, State
    location = Column(String, nullable=True)
    estimated_value = Column(Float, default=0.0)
    quantity_kg = Column(Float, default=0.0)
    deadline = Column(String, nullable=True)
    days_to_deadline = Column(Integer, nullable=True)
    status = Column(String, default="OPEN")   # OPEN, CLOSED, WON, LOST, SKIPPED
    eligibility_check = Column(String, nullable=True)   # ELIGIBLE, INELIGIBLE, CHECK_NEEDED
    notes = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    
    # New Government Tender Director extensions
    opportunity_score = Column(Integer, default=0)
    win_probability = Column(Integer, default=0)
    required_products = Column(String, nullable=True)
    suggested_pricing = Column(Float, default=0.0)
    expected_margin = Column(Float, default=0.0)
    risk_level = Column(String, nullable=True)  # Proceed, Proceed with caution, Do not bid
    proposal_text = Column(String, nullable=True)
    compliance_checklist = Column(String, nullable=True)
    missing_documents = Column(String, nullable=True)
    bid_strategy = Column(String, nullable=True)

    found_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Government Revenue Engine V1.2 extensions ──
    organization_id = Column(Integer, ForeignKey("gov_organizations.id"), nullable=True, index=True)
    # Contact intelligence — official, publicly available details only
    contact_officer = Column(String, nullable=True)
    official_email = Column(String, nullable=True)
    official_phone = Column(String, nullable=True)
    contact_confidence = Column(Integer, default=0)      # 0-100
    contact_verified = Column(Boolean, default=False)
    # Qualification scores (computed, never fabricated)
    product_match_pct = Column(Integer, default=0)
    bid_readiness_pct = Column(Integer, default=0)
    win_confidence_pct = Column(Integer, default=0)
    revenue_opportunity_score = Column(Float, default=0.0)
    emd_amount = Column(Float, default=0.0)
    msme_exemption = Column(Boolean, default=False)
    recommended_action = Column(String, nullable=True)   # APPLY | REVIEW | SKIP
    ai_analysis = Column(JSON, nullable=True)            # grounded tender analysis
    missing_documents_list = Column(JSON, nullable=True)


class ChannelScript(Base):
    """
    Founder pre-approval of an outreach script for one segment × channel
    (email / whatsapp / ai_call / linkedin / facebook). The Auto-Warm Engine
    will only warm a lead through a channel whose script the founder has
    approved beforehand — approve once, warm many.
    """
    __tablename__ = "channel_scripts"
    id = Column(Integer, primary_key=True, index=True)
    segment = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    approved = Column(Boolean, default=False)
    custom_body = Column(String, nullable=True)   # founder-edited script (overrides default)
    approved_by = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Organization(Base):
    """
    V1.1 Commercial Model (Architecture Freeze): the PRIMARY business entity.
    One row per real company/institution. Leads/opportunities hang off it —
    an organization is permanent; opportunities open and close.
    """
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    segment = Column(String, nullable=True)          # distributor / horeca / government / …
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    website = Column(String, nullable=True)
    # Verified contact intelligence (same gating rules as leads)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    whatsapp_number = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    phone_verified = Column(Boolean, default=False)
    email_verification_status = Column(String, default="UNVERIFIED")
    # Account economics (from real outcomes only)
    revenue_won = Column(Float, default=0.0)
    gross_margin_won = Column(Float, default=0.0)
    orders_count = Column(Integer, default=0)
    relationship_score = Column(Integer, default=0)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, nullable=True)

    # ── Company Intelligence (vFinal Domain Freeze §4) ──
    # Enrichment signals that directly improve opportunity ranking. Real data
    # only — every field stays NULL until a verified source fills it.
    annual_revenue = Column(Float, nullable=True)
    estimated_coffee_consumption_kg = Column(Float, nullable=True)   # monthly kg
    branches = Column(Integer, nullable=True)
    warehouse_count = Column(Integer, nullable=True)
    current_supplier = Column(String, nullable=True)
    competitors = Column(JSON, nullable=True)                        # list of names
    distribution_reach = Column(String, nullable=True)               # e.g. "Punjab + Haryana"
    product_categories = Column(JSON, nullable=True)                 # list
    expansion_plans = Column(String, nullable=True)
    buying_frequency = Column(String, nullable=True)                 # weekly / monthly / quarterly
    average_order_size = Column(Float, nullable=True)                # kg or ₹
    payment_behaviour = Column(String, nullable=True)               # advance / net-30 / delayed

    opportunities = relationship("RevenueOpportunity", back_populates="organization")
    contacts = relationship("Contact", back_populates="organization")


class Contact(Base):
    """
    vFinal Domain Freeze §3 — role-based Contact Intelligence.
    Contacts are a strategic asset, not flat email/phone fields. Each real
    person at a Company carries a role and relationship signals that make the
    system smarter over time. Real, verified data only (same gating as leads).
    """
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    name = Column(String, nullable=True)
    # Purchase Manager | Admin | Owner | Finance | Warehouse | Accounts | Procurement
    role = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    whatsapp_number = Column(String, nullable=True)
    # Verification (mirrors lead gating — a contact channel is only "real"
    # once a verified source confirms it)
    email_verification_status = Column(String, default="UNVERIFIED")
    phone_verified = Column(Boolean, default=False)
    # Relationship intelligence (built from real recorded interactions)
    preferred_channel = Column(String, nullable=True)   # email | whatsapp | call
    response_rate = Column(Float, default=0.0)          # 0-1, replies / touches
    last_contacted = Column(DateTime, nullable=True)
    relationship_score = Column(Integer, default=0)     # 0-100
    influence_score = Column(Integer, default=0)        # 0-100, buying-decision weight
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    organization = relationship("Organization", back_populates="contacts")


class RevenueOpportunity(Base):
    """
    V1.1 Commercial Model: the SECONDARY entity — one revenue pursuit at an
    organization. Bridges to the operational B2BLead row (lead_id) during the
    strangler migration; the Decision Engine keys off these going forward.
    """
    __tablename__ = "revenue_opportunities"
    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    kind = Column(String, default="NEW_BUSINESS")    # NEW_BUSINESS | TENDER | REORDER | UPSELL
    status = Column(String, default="OPEN")          # OPEN | WON | LOST
    stage = Column(String, nullable=True)            # mirrors lead status while bridged
    estimated_value = Column(Float, default=0.0)     # Expected Revenue
    expected_margin = Column(Float, default=0.0)
    probability = Column(Float, default=0.0)
    opened_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    # ── Revenue Opportunity economics (vFinal Domain Freeze §5) ──
    # This is the real business object: every opportunity carries the full
    # commercial picture so the Decision/Recommendation engines can rank by
    # margin per founder hour (North Star). Computed from facts, never guessed.
    founder_hours = Column(Float, nullable=True)             # effort to close
    expected_roi = Column(Float, nullable=True)              # margin / founder_hours
    expected_collection = Column(Float, nullable=True)       # cash expected to land
    expected_collection_date = Column(DateTime, nullable=True)
    expected_close_date = Column(DateTime, nullable=True)
    risk_level = Column(String, nullable=True)               # LOW | MEDIUM | HIGH
    commercial_stage = Column(String, nullable=True)         # Prospecting | Qualified | Sampling | Proposal | Negotiation | Won | Lost

    organization = relationship("Organization", back_populates="opportunities")
    lead = relationship("B2BLead", foreign_keys=[lead_id])


class GovOrganization(Base):
    """
    Government Revenue Engine: every government buyer is a permanent account,
    not a rediscovered lead. Only real, officially published data is stored —
    unknown fields stay empty.
    """
    __tablename__ = "gov_organizations"
    id = Column(Integer, primary_key=True, index=True)
    organization = Column(String, unique=True, index=True)
    department = Column(String, nullable=True)
    state = Column(String, nullable=True)
    district = Column(String, nullable=True)
    pincode = Column(String, nullable=True)
    website = Column(String, nullable=True)
    purchase_portal = Column(String, nullable=True)      # GeM / CPPP / State
    procurement_officer = Column(String, nullable=True)
    official_email = Column(String, nullable=True)
    official_phone = Column(String, nullable=True)
    # Relationship intelligence (built from actual recorded outcomes)
    tenders_found = Column(Integer, default=0)
    tenders_won = Column(Integer, default=0)
    tenders_lost = Column(Integer, default=0)
    revenue_won = Column(Float, default=0.0)
    last_tender_date = Column(DateTime, nullable=True)
    relationship_score = Column(Integer, default=0)      # 0-100, from real interactions
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GovTenderOutcome(Base):
    """
    Win/Loss learning store — one immutable record per completed tender.
    Feeds future recommendations; fields stay NULL when the reason is unknown
    (never guessed).
    """
    __tablename__ = "gov_tender_outcomes"
    id = Column(Integer, primary_key=True, index=True)
    tender_id = Column(Integer, ForeignKey("gov_tenders.id"), index=True)
    organization_id = Column(Integer, ForeignKey("gov_organizations.id"), nullable=True)
    result = Column(String)                              # WON | LOST | NOT_SUBMITTED
    reason = Column(String, nullable=True)
    competitor = Column(String, nullable=True)
    price_difference_pct = Column(Float, nullable=True)
    technical_reason = Column(String, nullable=True)
    documentation_issues = Column(String, nullable=True)
    founder_notes = Column(String, nullable=True)
    bid_value = Column(Float, default=0.0)
    margin_realized = Column(Float, default=0.0)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class WorkflowEvent(Base):
    """
    Immutable event store — Layer 2 of the Purity Beans Founder Revenue OS V1.1.
    Every significant business action appends here. Never updated, never deleted.
    """
    __tablename__ = "workflow_events"
    id = Column(Integer, primary_key=True, index=True)
    # What happened
    event_type = Column(String, nullable=False, index=True)
    # e.g. LEAD_CREATED, EMAIL_APPROVED, EMAIL_SENT, WHATSAPP_SENT,
    #       AI_CALL_STARTED, AI_CALL_COMPLETED, FOUNDER_CALL_COMPLETED,
    #       MEETING_BOOKED, SAMPLE_APPROVED, SAMPLE_DISPATCHED,
    #       PROPOSAL_SENT, ORDER_WON, PAYMENT_RECEIVED, REORDER_TRIGGERED,
    #       STATUS_CHANGED, DNC_SET, CONSENT_GIVEN, WORKFLOW_FAILED

    # Context
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    # vFinal Domain Freeze §1/§9: events key off the permanent Opportunity so
    # the full revenue-loop history (Opportunity Created → … → Reorder) survives
    # even after the operational lead row is retired by the strangler migration.
    opportunity_id = Column(Integer, ForeignKey("revenue_opportunities.id"), nullable=True, index=True)
    workflow_id = Column(String, nullable=True)   # UUID for a specific workflow run
    actor = Column(String, default="FOUNDER")     # FOUNDER | AI | SYSTEM
    channel = Column(String, nullable=True)       # email | whatsapp | call | system

    # State transition
    before_status = Column(String, nullable=True)
    after_status = Column(String, nullable=True)

    # Payload (all extra context)
    payload = Column(JSON, nullable=True)

    # Immutable timestamp
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class WorkflowExecution(Base):
    """
    Workflow Engine execution record — V1.1 lifecycle:
    REQUESTED → PENDING → EXECUTING → COMPLETED | FAILED (→ retry).
    All side effects (email, WhatsApp, calls, samples, proposals) are owned
    by the Workflow Engine and traced here; WorkflowEvent stores the
    immutable business events they produce.
    """
    __tablename__ = "workflow_executions"
    id = Column(Integer, primary_key=True, index=True)
    workflow_type = Column(String, nullable=False, index=True)
    # EMAIL_SEND | WHATSAPP_DRAFT | AI_CALL | FOUNDER_CALL | MEETING |
    # SAMPLE_DISPATCH | PROPOSAL_SEND | ORDER | REORDER | ENRICHMENT
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    status = Column(String, default="REQUESTED", index=True)
    # REQUESTED | PENDING | EXECUTING | COMPLETED | FAILED
    requested_by = Column(String, default="FOUNDER")   # FOUNDER | AI | SYSTEM
    payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    retry_count = Column(Integer, default=0)
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class OutreachReminder(Base):
    """
    Scheduled next-touch for the one-click warmup sequence (master spec §8).
    When the founder executes an approved outbound, the executor auto-schedules
    the next channel's reminder here (e.g. email today → WhatsApp in 3 days →
    AI call in 2 more). Reminders surface in the Action Queue / Founder Inbox
    when due. Reminders are the *plan*; WorkflowEvent stays the immutable record
    of what actually happened.
    """
    __tablename__ = "outreach_reminders"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("revenue_opportunities.id"), nullable=True, index=True)
    channel = Column(String, nullable=False)          # email | whatsapp | ai_call | founder_call
    sequence_step = Column(Integer, default=1)         # 1=email, 2=whatsapp, 3=ai_call, 4=founder_call
    due_at = Column(DateTime, nullable=False, index=True)
    reason = Column(String, nullable=True)
    status = Column(String, default="SCHEDULED", index=True)  # SCHEDULED | DONE | CANCELLED | SKIPPED
    created_at = Column(DateTime, default=datetime.utcnow)
    done_at = Column(DateTime, nullable=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


# Expose alias for OutboundEmail
OutboundEmail = EmailDraft


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id = Column(Integer, primary_key=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("revenue_opportunities.id"), nullable=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=True, index=True)
    type = Column(String)  # email | whatsapp | ai_call | proposal | discount | sample_dispatch | tender_submission | bulk_campaign
    status = Column(String, default="PENDING_FOUNDER")  # PENDING_FOUNDER | APPROVED | REJECTED | EXPIRED | EXECUTED
    expected_margin = Column(Float, default=0.0)
    expected_revenue = Column(Float, default=0.0)
    expected_roi = Column(Float, default=0.0)
    founder_time_required = Column(Float, default=0.0)  # hours
    draft_message = Column(String, nullable=True)
    draft_subject = Column(String, nullable=True)
    evidence = Column(JSON, nullable=True)  # list of strings
    approved_payload_hash = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)
    executed_by = Column(String, nullable=True)



class LeadEvidence(Base):
    """
    Truth Layer — one row per observed fact about why a business might (or
    might not) buy coffee.

    Rows are append-only and hold ONLY what was observed plus its provenance.
    No score, classification, next action or revenue estimate is ever stored
    here: those are derived, and derived values written back into the fact
    store go stale and become unexplainable. That failure was observed
    directly in this codebase — two different engines wrote coffee_buying_score
    onto the lead row, the second silently overwrote the first, and every
    verified distributor dropped to a score of 0.

    Scoring is therefore just an aggregation over these rows, recomputed on
    read (see services/opportunity_intelligence.py).
    """
    __tablename__ = "lead_evidence"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)

    # What was observed
    signal_type = Column(String, nullable=False, index=True)
    value = Column(String, nullable=True)          # "250", "Yes", "Tea & Coffee"

    # Where it came from — provenance is mandatory for a fact to count
    source = Column(String, nullable=False)        # "Google Maps", "Official Website"
    source_url = Column(String, nullable=True)
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # How much the source is trusted (0.0–1.0) and whether a human/system confirmed it
    confidence = Column(Float, default=0.5, nullable=False)
    verified = Column(Boolean, default=False)

    # Contribution to the buying score. Kept on the row so a weight change is
    # visible as new evidence rather than silently rewriting history.
    weight_positive = Column(Integer, default=0)
    weight_negative = Column(Integer, default=0)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class LeadInteraction(Base):
    """
    Business Memory — the permanent, append-only record of every interaction
    with a business.

    Distinct from WorkflowEvent, which records that a SYSTEM action occurred
    (an email left the server, a status changed). This records what was
    LEARNED: who the decision maker is, what they currently buy, what they
    said, and when to come back. That knowledge previously lived nowhere — a
    founder call taught the system nothing, so every future email, WhatsApp and
    call brief started from zero.

    Append-only by design. A correction does not overwrite: PATCH writes a new
    row carrying supersedes_id, and the original keeps superseded_by_id, so the
    full history of what was believed and when survives. Nothing is ever
    rewritten to satisfy a later validation rule.
    """
    __tablename__ = "lead_interactions"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)

    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, default="FOUNDER")     # FOUNDER | AI | EMPLOYEE | SYSTEM
    method = Column(String, nullable=False, index=True)  # email | whatsapp | founder_call |
                                                         # ai_call | meeting | visit | sample |
                                                         # proposal | tender | order | other
    outcome = Column(String, nullable=True, index=True)  # interested | not_interested | call_back ...

    # ── Structured CRM facts learned in this interaction ──
    decision_maker = Column(String, nullable=True, index=True)
    designation = Column(String, nullable=True)
    current_supplier = Column(String, nullable=True, index=True)
    current_brand = Column(String, nullable=True, index=True)
    monthly_consumption_kg = Column(Float, nullable=True)
    budget_range = Column(String, nullable=True)
    price_sensitivity = Column(String, nullable=True)     # low | medium | high
    interested = Column(Boolean, nullable=True)           # tri-state: yes / no / not established
    priority = Column(String, nullable=True)              # HIGH | MEDIUM | LOW
    preferred_contact_time = Column(String, nullable=True)
    preferred_contact_method = Column(String, nullable=True)
    next_followup_date = Column(String, nullable=True, index=True)

    remark = Column(String, nullable=True)                # free text, searchable

    # Correction trail — never an in-place edit.
    supersedes_id = Column(Integer, nullable=True)
    superseded_by_id = Column(Integer, nullable=True)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class InteractionAttachment(Base):
    """
    Files attached to an interaction — quotations, sample photos, business
    cards, voice notes, call-recording links. Append-only: a new upload never
    replaces an earlier one.
    """
    __tablename__ = "interaction_attachments"

    id = Column(Integer, primary_key=True, index=True)
    interaction_id = Column(Integer, ForeignKey("lead_interactions.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    kind = Column(String, nullable=True)        # pdf | image | voice_note | recording_link | other
    filename = Column(String, nullable=True)
    url = Column(String, nullable=True)         # link, or path under the uploads dir
    note = Column(String, nullable=True)
    uploaded_by = Column(String, default="FOUNDER")
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AgentCommission(Base):
    __tablename__ = "agent_commissions"

    id = Column(Integer, primary_key=True, index=True)
    agent_key = Column(String, nullable=False, index=True) # discovery_scoring, outreach_channels, proposal_negotiation, followup_closing
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    amount_earned = Column(Float, default=0.0)
    amount_spent = Column(Float, default=0.0)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


class AgentToolUpgrade(Base):
    __tablename__ = "agent_tool_upgrades"

    id = Column(Integer, primary_key=True, index=True)
    agent_key = Column(String, nullable=False, index=True)
    tool_name = Column(String, nullable=False)
    upgrade_cost = Column(Float, nullable=False)
    projected_uplift = Column(Float, nullable=False)
    roi_multiplier = Column(Float, nullable=False)
    status = Column(String, default="LOCKED") # LOCKED | APPROVED | PURCHASED
    description = Column(String, nullable=True)
    approved_at = Column(DateTime, nullable=True)


class AIAgentPerformance(Base):
    __tablename__ = "ai_agent_performance"

    agent_id = Column(String, primary_key=True, index=True)
    agent_name = Column(String, nullable=False)
    objective = Column(String, nullable=False)
    actions_completed = Column(Integer, default=0)
    opportunities_influenced = Column(Integer, default=0)
    qualified_replies = Column(Integer, default=0)
    meetings_generated = Column(Integer, default=0)
    samples_generated = Column(Integer, default=0)
    proposals_generated = Column(Integer, default=0)
    orders_influenced = Column(Integer, default=0)
    realised_revenue = Column(Float, default=0.0)
    realised_margin = Column(Float, default=0.0)
    founder_minutes_used = Column(Float, default=0.0)
    cost = Column(Float, default=0.0)
    roi = Column(Float, default=0.0)
    attribution_confidence = Column(String, default="LOW")


class AIGrowthTreasurySettings(Base):
    __tablename__ = "ai_growth_treasury_settings"

    id = Column(Integer, primary_key=True, index=True)
    reinvestment_rate = Column(Float, default=0.0) # default 0%
    reinvestment_enabled = Column(Boolean, default=False)
    bootstrap_mode = Column(Boolean, default=True) # default ON
    realised_revenue = Column(Float, default=0.0)
    realised_margin = Column(Float, default=0.0)
    ai_reinvestment_reserve = Column(Float, default=0.0)


class SubscriptionProposal(Base):
    __tablename__ = "subscription_proposals"

    id = Column(Integer, primary_key=True, index=True)
    tool_name = Column(String, nullable=False)
    agent_id = Column(String, nullable=False, index=True)
    bottleneck_removed = Column(String, nullable=False)
    monthly_cost = Column(Float, nullable=False)
    projected_uplift = Column(Float, nullable=False)
    payback_period_months = Column(Float, nullable=False)
    confidence = Column(String, default="LOW") # LOW | MEDIUM | HIGH
    justification_status = Column(String, default="NOT JUSTIFIED") # NOT JUSTIFIED | WATCH | FOUNDER REVIEW | FUNDABLE FROM AI RESERVE
    status = Column(String, default="PROPOSED") # PROPOSED | APPROVED | REJECTED | ACTIVE
    approved_at = Column(DateTime, nullable=True)


class LeadAttributionPath(Base):
    __tablename__ = "lead_attribution_paths"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("b2b_leads.id"), nullable=False, index=True)
    stage = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    weight = Column(Float, default=0.0)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    lead = relationship("B2BLead", foreign_keys=[lead_id])


# ── Draft admission control ───────────────────────────────────────────────────
# 1068 drafts sat awaiting approval; 17 could actually be sent. 896 of them —
# 84% — were written for businesses with NO EMAIL ADDRESS. The system composed a
# personalised pitch for each, queued it for a decision, and had nowhere to send
# it. That is not a queue with a filtering problem; it is a queue that should
# never have been populated.
#
# Eight call sites create EmailDraft rows. Gating them one by one is the exact
# mistake this codebase already made with send_email — "gating them individually
# left two ungated" — so the check lives at the single chokepoint every insert
# must pass through.
#
# Blocked drafts are CLASSIFIED, never dropped. The breakdown is the engineering
# target: 896 need contact discovery, 137 need trust promotion, 11 need
# verification. Deleting them would erase the only map of where revenue is stuck.

BLOCKED_NO_EMAIL       = "BLOCKED_NO_EMAIL"
BLOCKED_LOW_TRUST      = "BLOCKED_LOW_TRUST"
BLOCKED_LOW_CONFIDENCE = "BLOCKED_LOW_CONFIDENCE"
BLOCKED_SUPPRESSED     = "BLOCKED_SUPPRESSED"
DRAFT_BLOCKED_STATES   = (BLOCKED_NO_EMAIL, BLOCKED_LOW_TRUST,
                          BLOCKED_LOW_CONFIDENCE, BLOCKED_SUPPRESSED)


def _classify_draft_admission(lead) -> tuple[str | None, str]:
    """(blocked_status, why). None means the draft is genuinely actionable."""
    from app.services.trust_promoter import may_send, normalise
    ok, why = may_send(lead)
    if ok:
        return None, "sendable"
    t = normalise(getattr(lead, "email_trust", None))
    if not (getattr(lead, "email", "") or "").strip():
        return BLOCKED_NO_EMAIL, "no address on file — needs contact discovery"
    if t in ("PURGED", "BOUNCED", "INVALID", "EXPIRED"):
        return BLOCKED_SUPPRESSED, f"{t} — must not be contacted on this address"
    if "confidence" in why:
        return BLOCKED_LOW_CONFIDENCE, why
    return BLOCKED_LOW_TRUST, why


@event.listens_for(EmailDraft, "before_insert")
def _gate_draft_insert(mapper, connection, target):
    # A draft for a contact we cannot mail is not work. It is marked blocked so
    # it stops inflating the founder's queue while remaining countable.
    try:
        if target.status in DRAFT_BLOCKED_STATES:
            return                                  # already classified
        lead_id = getattr(target, "lead_id", None)
        if not lead_id:
            return
        from app.database.database import SessionLocal
        _s = SessionLocal()
        try:
            lead = _s.query(B2BLead).filter(B2BLead.id == lead_id).first()
            if lead is None:
                return
            blocked, why = _classify_draft_admission(lead)
            if blocked:
                target.status = blocked
                target.reason = f"{(target.reason or '')[:160]} | admission: {why}".strip(" |")
        finally:
            _s.close()
    except Exception as e:
        # Never let admission control break a write — but never swallow it
        # silently either, or this becomes the next invisible defect.
        print(f"[models] draft admission check failed for lead "
              f"{getattr(target, 'lead_id', '?')}: {e.__class__.__name__}: {e}")


# ── EMAIL_SENT must be provable ───────────────────────────────────────────────
# 90 EMAIL_SENT events existed; 4 carried both a recipient and a provider
# message-id. 79 had no SMTP evidence at all. Cadence, reply intelligence,
# account suppression and every KPI read that ledger, so 96% of the history
# driving real decisions could not support them.
#
# Three code paths wrote the event and each invented its own payload. Rather
# than police 78 call sites, the invariant lives where every insert must pass:
# an EMAIL_SENT without a recipient AND a provider message-id is renamed to
# EMAIL_SENT_UNPROVEN. Nothing is lost and nothing raises — but a record that
# cannot prove delivery can no longer masquerade as one, and every query
# counting EMAIL_SENT now counts only sends that actually happened.

EMAIL_SENT_UNPROVEN = "EMAIL_SENT_UNPROVEN"


@event.listens_for(WorkflowEvent, "before_insert")
def _require_send_proof(mapper, connection, target):
    try:
        if target.event_type != "EMAIL_SENT":
            return
        p = target.payload or {}
        to = str(p.get("to") or "").strip()
        mid = str(p.get("message_id") or "").strip()
        if to and mid:
            # Idempotency. A provider message-id identifies ONE delivery, so
            # recording it twice would double a touch count, advance the
            # cadence early and inflate every conversion rate downstream. A
            # worker retry after an SMTP timeout is the realistic way this
            # happens: the provider accepted the message, the acknowledgement
            # was lost, and the retry looks like a fresh send.
            from app.database.database import SessionLocal as _S
            _s = _S()
            try:
                _dupe = _s.query(WorkflowEvent).filter(
                    WorkflowEvent.event_type == "EMAIL_SENT").all()
                for _e in _dupe:
                    if str((_e.payload or {}).get("message_id") or "") == mid:
                        target.event_type = "EMAIL_SENT_DUPLICATE"
                        _p = dict(p)
                        _p["duplicate_of_event"] = _e.id
                        _p["note"] = ("this provider message-id is already "
                                      "recorded; one acceptance, one delivery")
                        target.payload = _p
                        return
            finally:
                _s.close()
            return                                  # provable and unique
        missing = [n for n, v in (("recipient", to), ("message_id", mid)) if not v]
        target.event_type = EMAIL_SENT_UNPROVEN
        p = dict(p)
        p["unproven_because"] = f"missing {', '.join(missing)}"
        p["note"] = ("recorded as a send but cannot prove delivery; excluded "
                     "from send counts and cadence")
        target.payload = p
    except Exception as e:
        # Never break a write, never swallow silently — a guard that fails
        # quietly becomes the next invisible defect.
        print(f"[models] send-proof check failed: {e.__class__.__name__}: {e}")


@event.listens_for(B2BLead.email, "set", active_history=True)
def _confidence_belongs_to_an_address(target, value, oldvalue, initiator):
    """Changing the address invalidates the confidence earned by the old one.

    email_confidence is a score about a specific mailbox — did it accept mail,
    did anyone reply, does the domain resolve. It is not a property of the
    business. But sixteen different call sites assign lead.email, and none of
    them reset it, so a harvested address silently inherited whatever the
    previous address had earned.

    That is not cosmetic. Sending is gated twice — email_trust in MAY_SEND AND
    email_confidence >= CONFIDENCE_FLOOR — so an inherited score satisfies half
    the gate for free. Twelve leads currently carry confidence >= 40 with no
    address at all, and website_harvester(only_missing=True) targets exactly
    those rows: it would have written a new address, granted VERIFIED, and both
    gates would have opened on a mailbox that earned neither.

    Guarding the attribute rather than the callers is deliberate. A rule
    enforced in sixteen places is a rule that will be missed in the
    seventeenth.
    """
    try:
        def _norm(v):
            return (v or "").strip().lower() if isinstance(v, str) else ""

        # oldvalue is a sentinel on a brand-new object or an unloaded attribute;
        # either way there is no earned score to protect.
        old = _norm(oldvalue) if isinstance(oldvalue, str) else ""
        new = _norm(value)
        if old == new:
            return value

        target.email_confidence = 0
        target.email_verified = False
        target.email_verified_at = None
        # Trust is not touched here. Demoting it is trust_promoter's decision,
        # and evaluate() will make it on the next sweep with the full evidence.
        # This only withdraws the half of the gate that was never earned.
    except Exception as e:
        print(f"[models] email-confidence guard failed: {e.__class__.__name__}: {e}")
    return value


@event.listens_for(B2BLead.whatsapp_number, "set", active_history=True, retval=True)
def _whatsapp_must_be_reachable(target, value, oldvalue, initiator):
    """WhatsApp cannot reach an STD landline. Refuse one at the attribute.

    retval=True is load-bearing, not decoration: without it SQLAlchemy ignores
    what a "set" listener returns and stores the original value anyway. The
    first version of this guard looked correct, ran on every write, and changed
    nothing.

    A 0172 / 0161 / 022 number reaches a desk and is a perfectly good number to
    CALL. It is not a WhatsApp number, and assigning it queues sends that can
    never arrive. 212 rows carried exactly that and had to be cleared by hand.

    Eight call sites assign whatsapp_number. Three of them had a guard, five
    did not, and the ones that did not were the ones nobody thought about --
    a manual edit endpoint that mirrored `phone` straight across, and two
    enrichment writes. Guarding here means the count of call sites stops
    mattering.

    is_landline() is imported rather than reimplemented so there is one
    definition of what a mobile looks like.
    """
    try:
        if not value:
            return value
        from app.services.contact_enricher import is_landline
        if is_landline(value):
            return None
    except Exception as e:
        # A guard that fails silently becomes the next invisible defect, and
        # one that raises breaks an unrelated write. Say so and let it through.
        print(f"[models] whatsapp landline guard failed: {e.__class__.__name__}: {e}")
    return value
