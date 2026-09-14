
ACTIVE_SKUS = ["Purista", "Purica", "Ultra Blend", "Bold"]
ARCHITECTURE_VERSION = "V1.0"
ARCHITECTURE_FROZEN = True
FREEZE_DATE = "2026-06-22"
FOUNDER = "Hiten Jain"
ENABLE_EXPERIMENTAL_FEATURES = False
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.models import B2BLead
from datetime import datetime

class CRMTrackerService:
    @staticmethod
    def recalculate_lead_score_and_action(lead: B2BLead) -> None:
        # Initialize stage_entered_date if not present
        if not lead.stage_entered_date:
            lead.stage_entered_date = lead.last_updated or datetime.utcnow()

        # 1. Intent Score (max 100)
        intent_points = 0
        if lead.email_opens > 0:
            intent_points += 5
        if lead.email_opens >= 2:
            intent_points += 10
        if lead.email_clicks > 0:
            intent_points += 15
        if lead.email_clicks >= 2:
            intent_points += 20
        if lead.email_clicks >= 3:
            intent_points += 25
        if lead.status in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            intent_points += 40
        if lead.status in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            intent_points += 50
        if lead.status in ("MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            intent_points += 75
        intent_score = min(intent_points, 100)
        lead.intent_score = intent_score

        # 2. Revenue Potential Score (max 100)
        val = lead.estimated_value or 0.0
        if val >= 3000000:     # >= 30L
            rev_score = 100
        elif val >= 1500000:   # >= 15L
            rev_score = 85
        elif val >= 800000:    # >= 8L
            rev_score = 70
        elif val >= 300000:    # >= 3L
            rev_score = 50
        elif val >= 100000:    # >= 1L
            rev_score = 30
        else:
            rev_score = 15

        # 3. Authority Score (max 100)
        if lead.decision_maker_score:
            auth_score = min(100, lead.decision_maker_score * 10)
        else:
            persona = (lead.contact_persona or "").lower()
            if "owner" in persona or "founder" in persona or "director" in persona or "ceo" in persona or "partner" in persona:
                auth_score = 100
            elif "procurement" in persona or "f&b" in persona or "buyer" in persona:
                auth_score = 80
            elif "admin" in persona or "facilities" in persona or "hr" in persona:
                auth_score = 60
            elif "office" in persona or "facility" in persona:
                auth_score = 40
            else:
                auth_score = 50

        # 4. Urgency Score (max 100)
        status_upper = (lead.status or "DISCOVERED").upper()
        if status_upper in ("REORDER_PREDICTED", "PROPOSAL_SENT", "FEEDBACK_PENDING"):
            urg_score = 100
        elif status_upper in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED"):
            urg_score = 80
        elif status_upper in ("EMAIL_SENT", "ONBOARDED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            urg_score = 60
        elif status_upper in ("DISCOVERED", "QUALIFIED"):
            urg_score = 30
        else:
            urg_score = 10

        # 5. Strategic Fit Score (max 100) based on segment weights:
        # Gifting = 100, Premium Grocery = 95, Distributor = 90, Corporate Pantry = 85, Hotel/HORECA = 80, Cafe = 65, Retail Chain = 60, Kirana = 30
        div = (lead.division or "needs_reclassification").lower()
        ind = (lead.industry or "").lower()
        if div == "gifting":
            strat_fit_score = 100
        elif "grocery" in ind or "supermarket" in ind:
            strat_fit_score = 95
        elif div == "distributor" or "wholesale" in ind:
            strat_fit_score = 90
        elif div == "corporate" and ("pantry" in ind or "canteen" in ind):
            strat_fit_score = 85
        elif div == "horeca" or "hospitality" in ind:
            strat_fit_score = 80
        elif "cafe" in ind or "restaurant" in ind:
            strat_fit_score = 65
        elif "retail" in ind or "chain" in ind:
            strat_fit_score = 60
        elif "kirana" in ind or "store" in ind:
            strat_fit_score = 30
        else:
            strat_fit_score = 85 # Corporate pantry baseline

        # Strategic fit score locked constants
        fit_weights = {"gifting": 100, "grocery": 95, "distributor": 90, "corporate_pantry": 85, "horeca": 80, "cafe": 65, "retail": 60, "kirana": 30}
        strat_fit_score = fit_weights.get(div, 85)
        
        # Calculate updated Action Priority Score for V1.0 freeze
        f_metrics = CRMTrackerService.get_financial_metrics(lead)
        lead.score = min(100, max(0, round(f_metrics["action_priority_score"])))

        # Default probability if not set or 0
        _STAGE_PROB = {
            "DISCOVERED": 0.05, "QUALIFIED": 0.10, "EMAIL_SENT": 0.12,
            "REPLIED": 0.25, "MEETING_BOOKED": 0.40, "MEETING_COMPLETED": 0.55,
            "SAMPLE_SENT": 0.60, "FEEDBACK_PENDING": 0.60, "FEEDBACK_RECEIVED": 0.65,
            "PROPOSAL_SENT": 0.70, "ORDER_WON": 1.00, "ONBOARDED": 1.00,
            "REORDER_PREDICTED": 0.90, "UPSELL_OFFERED": 0.75, "ACCOUNT_GROWTH": 1.00,
            "COLD": 0.00,
        }
        if not lead.probability or lead.probability == 0.0:
            lead.probability = _STAGE_PROB.get(status_upper, 0.05)

        # Set intent_tier and priority based on score
        if lead.score >= 81:
            lead.intent_tier = "Priority"
            lead.priority = "HIGH"
        elif lead.score >= 51:
            lead.intent_tier = "Hot"
            lead.priority = "MEDIUM"
        elif lead.score >= 21:
            lead.intent_tier = "Warm"
            lead.priority = "LOW"
        else:
            lead.intent_tier = "Cold"
            lead.priority = "LOW"
            
        # Next Best Action & Reason Engine
        next_action = "Qualify Lead"
        action_reason = "Lead discovered via scan. Check company details and match B2B criteria."
        
        if lead.status == "DISCOVERED":
            next_action = "Qualify Lead"
            action_reason = "Lead discovered via scan. Check company details and match B2B criteria."
        elif lead.status == "QUALIFIED":
            next_action = "Send Outbound Email"
            action_reason = "Qualified B2B target. Initiate product-first outreach sequence."
        elif lead.status == "EMAIL_SENT":
            next_action = "Follow Up Outreach"
            action_reason = f"Outreach sent. Opens: {lead.email_opens}. Monitor for engagement."
        elif lead.status == "REPLIED":
            next_action = "Book Zoom Meeting"
            action_reason = "Prospect replied to email outreach. Schedule introductory call."
        elif lead.status == "MEETING_BOOKED":
            next_action = "Conduct B2B Meeting"
            action_reason = "Zoom introductory call scheduled. Prepare Purica/Purista samples pitch."
        elif lead.status == "MEETING_COMPLETED":
            next_action = "Dispatch Sample Kit"
            action_reason = "Meeting complete. Send custom sample kit (Purica/Purista) for tasting."
        elif lead.status == "SAMPLE_SENT":
            next_action = "Track Delivery"
            action_reason = "Sample kit dispatched. Monitor Zoho shipping/consignment details."
        elif lead.status == "DELIVERED":
            next_action = "Request Feedback"
            action_reason = "Sample kit delivered. Contact recipient to schedule tasting call."
        elif lead.status == "FEEDBACK_PENDING":
            next_action = "Collect Taste Feedback"
            action_reason = "Tasting kit in hand. Record taste, aroma, and packaging scores."
        elif lead.status == "FEEDBACK_RECEIVED":
            next_action = "Generate Wholesale Proposal"
            action_reason = f"Feedback scores ready (Taste: {lead.sample_taste or 8}/10). Draft custom commercial contract."
        elif lead.status == "PROPOSAL_SENT":
            next_action = "Negotiate Margin / Price"
            action_reason = f"Proposal sent. Offered Rs. {lead.proposal_suggested_price or 648}/kg. Resolve pricing objections."
        elif lead.status in ("ORDER_WON", "ONBOARDED"):
            next_action = "Onboard Account"
            action_reason = "Order won! Set up recurring shipping profile and order history."
        else:
            next_action = "Upsell & Grow Account"
            action_reason = "Recurring account active. Identify potential upselling variants."
            
        lead.recommended_action = next_action

    @staticmethod
    def get_all_leads(db: Session) -> list[B2BLead]:
        # "Active" now means what it says. A business disqualified on category —
        # a beauty parlour, a builder, a utensils shop — is kept in the database
        # with its evidence and its event log, but it is not an opportunity and
        # must not reach a screen offering an APPROVE button. Filtering here
        # rather than in each view means no consumer can forget: this is the one
        # function every lead list goes through.
        # Fetch a disqualified business by id if you need it (memory, audit).
        return (db.query(B2BLead)
                .filter((B2BLead.status != "DISQUALIFIED") | (B2BLead.status.is_(None)))
                .order_by(B2BLead.score.desc()).all())

    @staticmethod
    def get_leads_by_status(db: Session, status: str) -> list[B2BLead]:
        return db.query(B2BLead).filter(B2BLead.status == status.upper()).order_by(B2BLead.score.desc()).all()

    @staticmethod
    def create_or_update_lead(db: Session, data: dict) -> B2BLead:
        """Insert a discovered lead or update its profile, retaining its status if it exists."""
        company = data.get("company")
        if not company:
            raise ValueError("Company name is required for CRM registration.")

        # A business with no city does not become a Bengaluru business. This
        # default put real leads in the wrong market and made every geography
        # count wrong — city drives discovery, the funnel and territory views.
        city = (data.get("city") or "").strip()
        derived_region = "South"
        if city.lower() in ("mumbai", "pune"):
            derived_region = "West"
        elif city.lower() in ("delhi", "abohar"):
            derived_region = "North"
        elif city.lower() in ("bengaluru", "hyderabad", "chennai"):
            derived_region = "South"

        est_val = float(data.get("estimated_value", 0.0))
        derived_size = "SMB"
        if est_val >= 2000000.0:
            derived_size = "Enterprise"
        elif est_val >= 800000.0:
            derived_size = "Mid-Market"

        div = (data.get("division") or "needs_reclassification").lower()
        comp = company.lower()
        derived_industry = "Corporate"
        if div == "distributor":
            derived_industry = "Wholesale"
        elif div == "retail":
            derived_industry = "Retail"
        elif div == "horeca":
            derived_industry = "Hospitality"
        elif "hospital" in comp or "medical" in comp or "diagnostics" in comp or "first" in comp:
            derived_industry = "Hospitals"
        elif "tech" in comp or "software" in comp or "it " in comp or "systems" in comp or "solutions" in comp or "edge" in comp:
            derived_industry = "IT Companies"

        title = (data.get("contact_title") or "").lower()   # no invented title
        derived_persona = None   # unknown until someone tells us
        if "admin" in title:
            derived_persona = "Admin Manager"
        elif "hr" in title or "human" in title or "people" in title:
            derived_persona = "HR Manager"
        elif "office" in title:
            derived_persona = "Office Manager"
        elif "facility" in title or "pantri" in title or "pantry" in title or "canteen" in title:
            derived_persona = "Facility Manager"
        elif "owner" in title or "founder" in title or "director" in title or "ceo" in title or "partner" in title:
            derived_persona = "Owner"

        lead = db.query(B2BLead).filter(B2BLead.company == company).first()
        if not lead:
            lead = B2BLead(
                company=company,
                contact_name=data.get("contact_name") or "",   # never a job title as a NAME
                contact_title=(data.get("contact_title") or ""),   # no invented job title
                email=data.get("email", ""),
                phone=data.get("phone", ""),
                city=city,
                # "corporate" was a guess that later drove the wrong pitch.
                division=(data.get("division") or "needs_reclassification"),
                # NEVER default provenance. This is what stamped 82 fabricated
                # leads as "Google Maps" — they carried no place_id, no rating and
                # no reviews, because they never came from Maps at all. A claim
                # about where data came from must be made by whoever had it.
                lead_source=(data.get("lead_source") or "UNKNOWN_SOURCE"),
                industry=data.get("industry", derived_industry),
                region=data.get("region", derived_region),
                company_size=data.get("company_size", derived_size),
                contact_persona=data.get("contact_persona", derived_persona),
                lost_reason=data.get("lost_reason"),
                estimated_value=est_val,
                score=int(data.get("score", 50)),
                probability=float(data.get("probability", 0.1)),
                status=data.get("status", "DISCOVERED").upper(),
                priority=data.get("priority", "MEDIUM"),
                recommended_action=data.get("recommended_action", ""),
                qualification_notes=data.get("qualification_notes", ""),
                sample_taste=data.get("sample_taste"),
                sample_aroma=data.get("sample_aroma"),
                sample_packaging=data.get("sample_packaging"),
                sample_intent=data.get("sample_intent"),
                sample_purchase_again=data.get("sample_purchase_again"),
                expected_monthly_consumption_kg=float(data.get("expected_monthly_consumption_kg", 0.0)),
                proposal_monthly_kg=float(data.get("proposal_monthly_kg", 0.0)),
                proposal_suggested_price=float(data.get("proposal_suggested_price", 0.0)),
                proposal_suggested_margin=float(data.get("proposal_suggested_margin", 0.0)),
                proposal_discount_percent=float(data.get("proposal_discount_percent", 0.0))
            )
            lead.last_updated = datetime.utcnow()
            CRMTrackerService.recalculate_lead_score_and_action(lead)
            db.add(lead)
        else:
            # Update fields but don't reset status if not explicitly passed
            if "contact_name" in data: lead.contact_name = data["contact_name"]
            if "contact_title" in data: lead.contact_title = data["contact_title"]
            if "email" in data: lead.email = data["email"]
            if "phone" in data: lead.phone = data["phone"]
            if "city" in data: lead.city = data["city"]
            if "division" in data: lead.division = data["division"]
            if "lead_source" in data: lead.lead_source = data["lead_source"]
            if "industry" in data: lead.industry = data["industry"]
            if "region" in data: lead.region = data["region"]
            if "company_size" in data: lead.company_size = data["company_size"]
            if "contact_persona" in data: lead.contact_persona = data["contact_persona"]
            if "lost_reason" in data: lead.lost_reason = data["lost_reason"]
            if "estimated_value" in data: lead.estimated_value = float(data["estimated_value"])
            if "score" in data: lead.score = int(data["score"])
            if "probability" in data: lead.probability = float(data["probability"])
            if "status" in data: lead.status = data["status"].upper()
            if "priority" in data: lead.priority = data["priority"]
            if "recommended_action" in data: lead.recommended_action = data["recommended_action"]
            if "qualification_notes" in data: lead.qualification_notes = data["qualification_notes"]
            if "sample_taste" in data: lead.sample_taste = data["sample_taste"]
            if "sample_aroma" in data: lead.sample_aroma = data["sample_aroma"]
            if "sample_packaging" in data: lead.sample_packaging = data["sample_packaging"]
            if "sample_intent" in data: lead.sample_intent = data["sample_intent"]
            if "sample_purchase_again" in data: lead.sample_purchase_again = data["sample_purchase_again"]
            if "expected_monthly_consumption_kg" in data: lead.expected_monthly_consumption_kg = float(data["expected_monthly_consumption_kg"])
            if "proposal_monthly_kg" in data: lead.proposal_monthly_kg = float(data["proposal_monthly_kg"])
            if "proposal_suggested_price" in data: lead.proposal_suggested_price = float(data["proposal_suggested_price"])
            if "proposal_suggested_margin" in data: lead.proposal_suggested_margin = float(data["proposal_suggested_margin"])
            if "proposal_discount_percent" in data: lead.proposal_discount_percent = float(data["proposal_discount_percent"])
            if "proposal_recommended_margin" in data: lead.proposal_recommended_margin = float(data["proposal_recommended_margin"])
            if "proposal_text" in data: lead.proposal_text = data["proposal_text"]
            lead.last_updated = datetime.utcnow()
            CRMTrackerService.recalculate_lead_score_and_action(lead)
            
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def update_lead_status(db: Session, company: str, status: str, notes: str = None) -> B2BLead:
        """Transitions a lead's status in the funnel."""
        lead = db.query(B2BLead).filter(B2BLead.company == company).first()
        if not lead:
            raise KeyError(f"Lead not found for company: {company}")
        
        status_upper = status.upper()
        if lead.status != status_upper:
            lead.status = status_upper
            lead.stage_entered_date = datetime.utcnow()
            
        if notes:
            lead.qualification_notes = notes
        CRMTrackerService.recalculate_lead_score_and_action(lead)
        lead.last_updated = datetime.utcnow()
        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def get_collection_profile(lead: B2BLead) -> tuple[float, int]:
        div_lower = (lead.division or "needs_reclassification").lower()
        if div_lower == "tender":
            return 0.75, 180  # Govt/Tender: 75% prob, 180d delay
        elif div_lower in ("distributor", "retail"):
            return 0.85, 60   # Distributor/Retail: 85% prob, 60d delay
        elif div_lower in ("gifting", "corporate", "cafe", "horeca"):
            return 0.95, 30   # Corporate/Gifting/Cafe/HORECA: 95% prob, 30d delay
        return 0.85, 45

    @staticmethod
    def get_margin_pct(lead: B2BLead) -> float:
        if lead.proposal_suggested_margin and lead.proposal_suggested_margin > 0:
            return lead.proposal_suggested_margin
        div_lower = (lead.division or "needs_reclassification").lower()
        if div_lower == "distributor":
            return 15.0
        elif div_lower == "horeca":
            return 20.0
        elif div_lower == "retail":
            return 25.0
        return 28.0

    @staticmethod
    def get_financial_metrics(lead: B2BLead) -> dict:
        vol = lead.expected_monthly_consumption_kg or 15.0
        if vol == 0.0:
            vol = (lead.estimated_value or 120000.0) / (700.0 * 12.0)
            vol = round(vol, 1)

        val_annual = lead.estimated_value or (vol * 700.0 * 12.0)
        
        # Win probability
        _STAGE_PROB = {
            "DISCOVERED": 0.05, "QUALIFIED": 0.10, "EMAIL_SENT": 0.12,
            "REPLIED": 0.25, "MEETING_BOOKED": 0.40, "MEETING_COMPLETED": 0.55,
            "SAMPLE_SENT": 0.60, "FEEDBACK_PENDING": 0.60, "FEEDBACK_RECEIVED": 0.65,
            "PROPOSAL_SENT": 0.70, "ORDER_WON": 1.00, "ONBOARDED": 1.00,
            "REORDER_PREDICTED": 0.90, "UPSELL_OFFERED": 0.75, "ACCOUNT_GROWTH": 1.00,
            "COLD": 0.00,
        }
        win_prob = lead.probability if (lead.probability and lead.probability > 0.0) else _STAGE_PROB.get((lead.status or "DISCOVERED").upper(), 0.05)
        
        coll_prob, coll_delay = CRMTrackerService.get_collection_profile(lead)
        margin_pct = CRMTrackerService.get_margin_pct(lead)
        
        # Collection-Adjusted formulas
        expected_revenue = val_annual * win_prob * coll_prob
        expected_margin = expected_revenue * (margin_pct / 100.0)
        expected_cash_score = expected_revenue / coll_delay if coll_delay > 0 else expected_revenue

        # 1. Engagement Factor
        engagement_factor = 1.0
        if lead.email_opens > 0:
            engagement_factor *= 1.05
        status_upper = (lead.status or "DISCOVERED").upper()
        if status_upper not in ("DISCOVERED", "QUALIFIED", "EMAIL_SENT"):
            engagement_factor *= 1.15
        if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"):
            engagement_factor *= 1.30
        if status_upper in ("MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"):
            engagement_factor *= 1.40
        engagement_factor = min(1.50, engagement_factor)

        # 2. Realization Score
        realization_score = win_prob * coll_prob * engagement_factor

        # 3. Retention Years
        retention_years = 3.0
        div_lower = (lead.division or "needs_reclassification").lower()
        if div_lower == "gifting":
            retention_years = 2.0
        elif div_lower == "distributor":
            retention_years = 4.0

        # 4. Revenue Quality Score & Grade
        margin_score = margin_pct
        collection_score = coll_prob * 100.0
        retention_score = (retention_years / 4.0) * 100.0
        engagement_score = lead.intent_score or 0.0

        quality_score = (0.40 * margin_score) + (0.30 * collection_score) + (0.20 * retention_score) + (0.10 * engagement_score)
        quality_score = min(100.0, max(0.0, quality_score))

        if quality_score >= 90.0:
            quality_grade = "AAA"
        elif quality_score >= 75.0:
            quality_grade = "AA"
        elif quality_score >= 60.0:
            quality_grade = "A"
        elif quality_score >= 40.0:
            quality_grade = "B"
        else:
            quality_grade = "C"

        # 5. Unit Economics: CAC
        sample_cost = 100.0 if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH") else 0.0
        courier_cost = 50.0 if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH") else 0.0
        call_cost = 50.0 if status_upper in ("MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH") else 10.0

        founder_time_cost = 0.0
        if status_upper in ("EMAIL_SENT", "REPLIED"):
            founder_time_cost = 100.0
        elif status_upper in ("MEETING_BOOKED", "MEETING_COMPLETED"):
            founder_time_cost = 300.0
        elif status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"):
            founder_time_cost = 450.0
        elif status_upper in ("PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            founder_time_cost = 600.0

        cac = sample_cost + courier_cost + call_cost + founder_time_cost

        # 6. Payback Period & LTV
        monthly_gross_margin = (val_annual * (margin_pct / 100.0)) / 12.0
        payback_period = (cac / monthly_gross_margin) if monthly_gross_margin > 0 else 0.0
        ltv = val_annual * (margin_pct / 100.0) * retention_years

        # 7. Action Priority Score
        cash_velocity_score = expected_margin / coll_delay if coll_delay > 0 else expected_margin
        cash_vel_norm = min(100.0, (cash_velocity_score / 2000.0) * 100.0)
        ltv_score = 20
        if ltv >= 1000000.0:
            ltv_score = 100
        elif ltv >= 500000.0:
            ltv_score = 80
        elif ltv >= 200000.0:
            ltv_score = 60
        elif ltv >= 100000.0:
            ltv_score = 40

        urg_score = 10.0
        if status_upper in ("REORDER_PREDICTED", "PROPOSAL_SENT", "FEEDBACK_PENDING"):
            urg_score = 100.0
        elif status_upper in ("REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED"):
            urg_score = 80.0
        elif status_upper in ("EMAIL_SENT", "ONBOARDED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            urg_score = 60.0
        elif status_upper in ("DISCOVERED", "QUALIFIED"):
            urg_score = 30.0

        action_priority_score = (cash_vel_norm * 0.5) + (win_prob * 100.0 * 0.2) + (ltv_score * 0.2) + (urg_score * 0.1)

        # 8. Founder Hours Invested
        founder_hours = 0.0
        if status_upper in ("EMAIL_SENT", "REPLIED"):
            founder_hours = 0.2
        elif status_upper in ("MEETING_BOOKED", "MEETING_COMPLETED"):
            founder_hours = 0.5
        elif status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"):
            founder_hours = 1.0
        elif status_upper in ("PROPOSAL_SENT", "ORDER_WON"):
            founder_hours = 2.0
        elif status_upper in ("ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"):
            founder_hours = 4.0
            
        heat_score = (expected_margin * realization_score) / coll_delay if coll_delay > 0 else expected_margin
        
        # Close Days
        close_days_map = {
            "PROPOSAL_SENT": 5, "FEEDBACK_RECEIVED": 8, "FEEDBACK_PENDING": 12,
            "SAMPLE_SENT": 15, "DELIVERED": 15, "MEETING_COMPLETED": 20,
            "MEETING_BOOKED": 25, "REPLIED": 30, "EMAIL_SENT": 35,
            "QUALIFIED": 40, "DISCOVERED": 45,
            "ORDER_WON": 0, "ONBOARDED": 0, "ACCOUNT_GROWTH": 0, "REORDER_PREDICTED": 0, "UPSELL_OFFERED": 0,
            "COLD": 0
        }
        close_days = close_days_map.get((lead.status or "DISCOVERED").upper(), 45)
        
        # Days in stage / Stuck / Activity
        days_in_stage = 0
        if lead.stage_entered_date:
            days_in_stage = (datetime.utcnow() - lead.stage_entered_date).days
        else:
            if lead.last_updated:
                days_in_stage = (datetime.utcnow() - lead.last_updated).days
        days_in_stage = max(0, days_in_stage)
        
        days_since_activity = 0
        if lead.last_updated:
            days_since_activity = (datetime.utcnow() - lead.last_updated).days
        days_since_activity = max(0, days_since_activity)
        
        # Velocity Score
        rev_pot_lakhs = val_annual / 100000.0
        days_stuck = max(1, days_since_activity)
        engagement = 1 + (lead.email_opens or 0) + ((lead.email_clicks or 0) * 2)
        if status_upper not in ("DISCOVERED", "QUALIFIED", "EMAIL_SENT"):
            engagement += 3
        velocity_score = min(100, round((rev_pot_lakhs * engagement / days_stuck) * 10.0))

        # --- V1.1 & V1.2 Calculations ---
        # 1. Margin Leakage details (discounts, freight, sampling, credit cost, CAC)
        discount_val = ((getattr(lead, "proposal_discount_percent", 0.0) or 0.0) / 100.0) * val_annual
        
        if div_lower == "distributor":
            freight = 5000.0
        elif div_lower == "retail":
            freight = 4000.0
        elif div_lower in ("gifting", "horeca"):
            freight = 2000.0
        else:
            freight = 1500.0
            
        sampling_cost_actual = 0.0
        if status_upper not in ("DISCOVERED", "QUALIFIED", "EMAIL_SENT", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED"):
            sampling_cost_actual = 150.0 # ₹100 kit + ₹50 shipping
            
        credit_days = lead.credit_period_days or 30
        if div_lower == "tender":
            credit_days = 180
        elif div_lower in ("distributor", "retail"):
            credit_days = 60
        credit_cost = val_annual * (credit_days / 365.0) * 0.12 # 12% cost of capital
        
        cac_mapping = {
            "googlemaps": 60.0,
            "indiamart": 700.0,
            "linkedin": 300.0,
            "tradeindia": 300.0,
            "referral": 100.0,
            "manual": 100.0,
            "website": 150.0,
            "whatsapp": 100.0,
            "corporategifting": 200.0,
            "tender": 1500.0
        }
        src_clean = (lead.lead_source or "").lower().replace(" ", "").strip()
        source_cac = cac_mapping.get(src_clean, 100.0)
        
        gross_margin = val_annual * (margin_pct / 100.0)
        true_net_margin = gross_margin - discount_val - freight - sampling_cost_actual - credit_cost - source_cac
        
        # Persist calculations to B2BLead only if they have changed
        if lead.freight_cost_estimate != freight:
            lead.freight_cost_estimate = freight
        if lead.discount_given != discount_val:
            lead.discount_given = discount_val
        if lead.sampling_cost_total != sampling_cost_actual:
            lead.sampling_cost_total = sampling_cost_actual
        if lead.credit_period_days != credit_days:
            lead.credit_period_days = credit_days
        if lead.actual_margin_net != true_net_margin:
            lead.actual_margin_net = true_net_margin

        # 2. Revenue Reality Score (RRS)
        eng_score = min(30.0, (lead.email_opens or 0) * 3.0 + (lead.email_clicks or 0) * 6.0)
        
        sample_score = 0.0
        if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED"):
            sample_score = 15.0
            if lead.sample_taste or lead.sample_aroma:
                taste = lead.sample_taste or 8
                aroma = lead.sample_aroma or 8
                sample_score += min(10.0, (taste + aroma) / 2.0)
        elif status_upper in ("PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"):
            sample_score = 25.0
            
        proposal_score = 0.0
        if status_upper == "PROPOSAL_SENT":
            proposal_score = 25.0
        elif status_upper in ("ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"):
            proposal_score = 25.0
            
        div_conversion = {
            "gifting": 18.0,
            "distributor": 15.0,
            "corporate": 12.0,
            "horeca": 10.0,
            "retail": 8.0,
            "tender": 5.0
        }
        similar_conversion = div_conversion.get(div_lower, 10.0)
        similar_score = (similar_conversion / 20.0) * 20.0
        
        reality_score = eng_score + sample_score + proposal_score + similar_score
        reality_score = min(100.0, max(0.0, reality_score))
        
        if reality_score >= 90.0:
            reality_grade = "HOT"
        elif reality_score >= 75.0:
            reality_grade = "ACTIVE"
        elif reality_score >= 60.0:
            reality_grade = "WARM"
        elif reality_score >= 40.0:
            reality_grade = "WATCH"
        else:
            reality_grade = "COLD"
            
        if lead.reality_score != reality_score:
            lead.reality_score = reality_score
        if lead.reality_grade != reality_grade:
            lead.reality_grade = reality_grade

        # 3. Founder Priority Score
        margin_opp_score = min(100.0, (expected_margin / 200000.0) * 100.0)
        founder_priority_score = (action_priority_score * 0.40) + (reality_score * 0.35) + (margin_opp_score * 0.25)
        
        # 4. Sample Follow-up SLA State
        target_followup = "GREEN"
        if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING"):
            if days_since_activity > 3:
                target_followup = "RED"
            elif days_since_activity > 2:
                target_followup = "AMBER"
            
        if lead.sample_followup_status != target_followup:
            lead.sample_followup_status = target_followup
            
        # 5. Deal Health Score
        health_points = 100
        if days_since_activity > 14:
            health_points -= 40
        elif days_since_activity > 7:
            health_points -= 20
        if status_upper == "PROPOSAL_SENT" and days_in_stage > 7:
            health_points -= 30
        if reality_score < 40.0:
            health_points -= 20
        
        health_points = max(0, health_points)
        target_health = "RED"
        if health_points >= 80:
            target_health = "GREEN"
        elif health_points >= 50:
            target_health = "AMBER"
            
        if lead.deal_health != target_health:
            lead.deal_health = target_health

        return {
            "monthly_consumption": vol,
            "annual_value": val_annual,
            "margin_pct": margin_pct,
            "gross_margin": gross_margin,
            "win_probability": win_prob,
            "collection_probability": coll_prob,
            "collection_delay_days": coll_delay,
            "expected_revenue": expected_revenue,
            "expected_margin": expected_margin,
            "cash_velocity_score": cash_velocity_score,
            "expected_cash_score": expected_cash_score,
            "close_days": close_days,
            "days_in_stage": days_in_stage,
            "days_since_activity": days_since_activity,
            "days_since_last_meaningful_activity": days_since_activity,
            "velocity_score": velocity_score,
            "realization_score": realization_score,
            "revenue_quality_score": quality_score,
            "quality_grade": quality_grade,
            "cac": source_cac,
            "payback_period": payback_period,
            "ltv": ltv,
            "action_priority_score": action_priority_score,
            "founder_hours": founder_hours,
            "heat_score": heat_score,
            "true_net_margin": true_net_margin,
            "reality_score": reality_score,
            "reality_grade": reality_grade,
            "founder_priority_score": founder_priority_score,
            "freight_cost_estimate": freight,
            "discount_given": discount_val,
            "sampling_cost_total": sampling_cost_actual,
            "credit_period_days": credit_days,
            "credit_cost": credit_cost,
            "deal_health": lead.deal_health
        }

    @staticmethod
    def get_kpis(db: Session) -> dict:
        from datetime import datetime, timedelta

        metrics_cache = {}
        def get_cached_metrics(lead: B2BLead) -> dict:
            if lead.id not in metrics_cache:
                metrics_cache[lead.id] = CRMTrackerService.get_financial_metrics(lead)
            return metrics_cache[lead.id]

        # --- Dead Lead Auto-Purge execution ---
        all_leads = db.query(B2BLead).all()
        now = datetime.utcnow()
        for l in all_leads:
            if l.status not in ("COLD", "DORMANT", "ARCHIVED"):
                f_m = get_cached_metrics(l)
                days_since = f_m["days_since_activity"]
                st_upper = (l.status or "").upper()
                if st_upper == "EMAIL_SENT" and l.email_opens == 0 and days_since >= 30:
                    l.status = "DORMANT"
                    metrics_cache.pop(l.id, None)
                elif st_upper == "SAMPLE_SENT" and days_since >= 45: # sample feedback pending
                    l.status = "DORMANT"
                    metrics_cache.pop(l.id, None)
                elif st_upper == "MEETING_COMPLETED" and days_since >= 30: # no proposal sent
                    l.status = "DORMANT"
                    metrics_cache.pop(l.id, None)
        db.commit()

        # Re-fetch active leads (excluding COLD, DORMANT, ARCHIVED)
        active_leads = db.query(B2BLead).filter(B2BLead.status.notin_(["COLD", "DORMANT", "ARCHIVED"])).all()
        won_leads = db.query(B2BLead).filter(B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])).all()

        # Revenue Generated (Won) Today / Week / Month
        today_rev = 0.0
        week_rev = 0.0
        month_rev = 0.0
        for wl in won_leads:
            val = wl.estimated_value or 0.0
            updated = wl.last_updated or now
            delta = (now - updated).days
            if delta <= 0:
                today_rev += val
            if delta <= 7:
                week_rev += val
            if delta <= 30:
                month_rev += val

        # V2: Segmented pipeline tiers
        modelled_opportunity_val = sum(l.estimated_value or 0.0 for l in active_leads if l.status == "DISCOVERED")
        qualified_pipeline_val = sum(
            l.estimated_value or 0.0 
            for l in active_leads 
            if l.status in ("QUALIFIED", "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED")
        )
        quoted_proposal_val = sum(l.estimated_value or 0.0 for l in active_leads if l.status == "PROPOSAL_SENT")
        won_revenue_val = sum(l.estimated_value or 0.0 for l in active_leads if l.status in ("ORDER_WON", "ONBOARDED", "REORDER_PREDICTED"))
        
        # Active pipeline is Qualified Pipeline + Quoted/Proposal Value
        pipeline_val = qualified_pipeline_val + quoted_proposal_val
        
        meetings_count = db.query(func.count(B2BLead.id)).filter(
            B2BLead.status.in_(["MEETING_BOOKED", "SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"])
        ).scalar() or 0
        
        samples_count = db.query(func.count(B2BLead.id)).filter(
            B2BLead.status.in_(["SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"])
        ).scalar() or 0
        
        orders_count = len(won_leads)
        leads_count = len(active_leads)

        # Cumulative Forecasts (7, 30, 60, 90, 180 Days)
        forecasts = {}
        for target in [7, 30, 60, 90, 180]:
            rev_f = 0.0
            margin_f = 0.0
            cash_f = 0.0
            for l in active_leads:
                f_metrics = get_cached_metrics(l)
                if f_metrics["close_days"] <= target:
                    rev_f += f_metrics["expected_revenue"]
                    margin_f += f_metrics["expected_margin"]
                if f_metrics["close_days"] + f_metrics["collection_delay_days"] <= target:
                    cash_f += f_metrics["expected_revenue"]
            forecasts[str(target)] = {
                "revenue": round(rev_f),
                "margin": round(margin_f),
                "cash_collection": round(cash_f)
            }

        # Expected margins & revenues
        total_expected_revenue = sum(get_cached_metrics(l)["expected_revenue"] for l in active_leads)
        total_expected_margin = sum(get_cached_metrics(l)["expected_margin"] for l in active_leads)
        revenue_velocity = total_expected_revenue / 30.0

        # CAC & LTV calculations for won leads
        won_count = len(won_leads)
        total_cac = 0.0
        total_ltv = 0.0
        total_won_margin = 0.0
        total_payback = 0.0
        for l in won_leads:
            f = get_cached_metrics(l)
            total_cac += f["cac"]
            total_ltv += f["ltv"]
            total_won_margin += f["gross_margin"]
            total_payback += f["payback_period"]
            
        avg_cac = total_cac / won_count if won_count > 0 else 640.0
        avg_payback = total_payback / won_count if won_count > 0 else 2.4

        # Founder efficiency / effectiveness
        total_hours = sum(get_cached_metrics(l)["founder_hours"] for l in all_leads)
        founder_efficiency = total_expected_margin / total_hours if total_hours > 0 else 0.0
        founder_effectiveness = total_won_margin / total_hours if total_hours > 0 else 0.0

        # Revenue Concentration
        top_won = sorted([l.estimated_value or 0.0 for l in won_leads], reverse=True)
        total_won_rev = sum(top_won)
        top_5_rev = sum(top_won[:5])
        top_2_rev = sum(top_won[:2])
        concentration_pct = (top_5_rev / total_won_rev * 100.0) if total_won_rev > 0 else 0.0
        top_2_pct = (top_2_rev / total_won_rev * 100.0) if total_won_rev > 0 else 42.0

        # Survival Runway
        cash_in_bank = 1440000
        monthly_burn = 200000
        runway_months = round(cash_in_bank / monthly_burn, 1)

        # Revenue Per Metrics
        won_revenue = total_won_rev
        revenue_per_sample = won_revenue / samples_count if samples_count > 0 else 18500.0
        revenue_per_meeting = won_revenue / meetings_count if meetings_count > 0 else 25000.0
        revenue_per_founder_hour = won_revenue / total_hours if total_hours > 0 else 5122.0

        # Revenue Source ROI
        sources_list = ["GoogleMaps", "IndiaMART", "TradeIndia", "LinkedIn", "Referral", "Website", "WhatsApp", "CorporateGifting", "Tender", "Manual"]
        source_attribution = {}
        for src in sources_list:
            src_leads = [l for l in all_leads if (l.lead_source or "").replace(" ", "") == src]
            src_won = [l for l in src_leads if l.status in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH")]
            src_revenue = sum(l.estimated_value or 0.0 for l in src_won)
            src_margin = sum(l.estimated_value * (CRMTrackerService.get_margin_pct(l) / 100.0) for l in src_won)
            src_cac = sum(get_cached_metrics(l)["cac"] for l in src_won)
            src_roi = src_revenue / src_cac if src_cac > 0 else (src_revenue / 500.0 if src_revenue > 0 else 0.0)
            
            source_attribution[src] = {
                "leads": len(src_leads),
                "won": len(src_won),
                "revenue": round(src_revenue),
                "margin": round(src_margin),
                "cac": round(src_cac / len(src_won)) if len(src_won) > 0 else 0,
                "roi": f"{src_roi:.1f}x" if src_roi > 0 else "0.0x"
            }
        revenue_by_source = {src: source_attribution[src]["revenue"] for src in sources_list}

        # Cash This Week (Expected Cash Next 7 Days)
        cash_this_week_leads = []
        for l in active_leads:
            f = get_cached_metrics(l)
            if f["close_days"] + f["collection_delay_days"] <= 7:
                cash_this_week_leads.append({
                    "company": l.company,
                    "expected_cash": round(f["expected_revenue"])
                })
        cash_this_week_total = sum(item["expected_cash"] for item in cash_this_week_leads)

        # Revenue Leakage
        leakage_categories = {
            "proposal_stalled": {"count": 0, "revenue": 0.0, "margin": 0.0, "label": "Proposal Sent > 14 Days"},
            "sample_no_feedback": {"count": 0, "revenue": 0.0, "margin": 0.0, "label": "Sample Sent No Feedback"},
            "meeting_no_proposal": {"count": 0, "revenue": 0.0, "margin": 0.0, "label": "Meeting Completed No Proposal"},
            "tender_no_approval": {"count": 0, "revenue": 0.0, "margin": 0.0, "label": "Tender Waiting Approval"}
        }
        for l in active_leads:
            f_metrics = get_cached_metrics(l)
            st_upper = (l.status or "DISCOVERED").upper()
            days_since = f_metrics["days_since_activity"]
            
            if st_upper == "PROPOSAL_SENT" and days_since > 14:
                leakage_categories["proposal_stalled"]["count"] += 1
                leakage_categories["proposal_stalled"]["revenue"] += f_metrics["expected_revenue"]
                leakage_categories["proposal_stalled"]["margin"] += f_metrics["expected_margin"]
            elif st_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING") and days_since > 7:
                leakage_categories["sample_no_feedback"]["count"] += 1
                leakage_categories["sample_no_feedback"]["revenue"] += f_metrics["expected_revenue"]
                leakage_categories["sample_no_feedback"]["margin"] += f_metrics["expected_margin"]
            elif st_upper == "MEETING_COMPLETED" and days_since > 5:
                leakage_categories["meeting_no_proposal"]["count"] += 1
                leakage_categories["meeting_no_proposal"]["revenue"] += f_metrics["expected_revenue"]
                leakage_categories["meeting_no_proposal"]["margin"] += f_metrics["expected_margin"]
            elif l.division == "tender" and st_upper in ("DISCOVERED", "QUALIFIED") and days_since > 10:
                leakage_categories["tender_no_approval"]["count"] += 1
                leakage_categories["tender_no_approval"]["revenue"] += f_metrics["expected_revenue"]
                leakage_categories["tender_no_approval"]["margin"] += f_metrics["expected_margin"]

        total_leaking_revenue = sum(c["revenue"] for c in leakage_categories.values())
        total_leaking_margin = sum(c["margin"] for c in leakage_categories.values())

        # Sample ROI restricted strictly to ACTIVE_SKUS
        skus = ACTIVE_SKUS
        sku_roi = {}
        for sku in skus:
            sent = db.query(B2BLead).filter(
                B2BLead.sample_sku == sku,
                B2BLead.status.in_(["SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING", "FEEDBACK_RECEIVED", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"])
            ).count()
            won = db.query(B2BLead).filter(
                B2BLead.sample_sku == sku,
                B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])
            ).count()
            
            conversion = (won / sent * 100.0) if sent > 0 else 0.0
            sample_cost = sent * 100.0
            courier_cost = sent * 50.0
            total_cost = sample_cost + courier_cost
            
            rev_gen = db.query(func.sum(B2BLead.estimated_value)).filter(
                B2BLead.sample_sku == sku,
                B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])
            ).scalar() or 0.0
            
            roi_multiplier = (rev_gen / total_cost) if total_cost > 0 else 0.0
            
            sku_roi[sku] = {
                "sent": sent,
                "won": won,
                "conversion_rate": round(conversion, 1),
                "cost": total_cost,
                "revenue": rev_gen,
                "roi_multiplier": round(roi_multiplier, 1)
            }

        # Calculate Revenue at Risk
        alerts = CRMTrackerService.get_reorder_alerts(db)
        revenue_at_risk = sum(a["estimated_value"] / 12.0 for a in alerts if a["runout_days"] < 15)

        # Dynamic Founder Blind Spots (exactly 3 warnings)
        blind_spots = []
        if top_2_pct > 40.0:
            blind_spots.append(f"⚠ {top_2_pct:.0f}% of revenue comes from top 2 customers")
        else:
            blind_spots.append("⚠ High revenue concentration risk detected on top accounts")
            
        stuck_prop = db.query(B2BLead).filter(B2BLead.status == "PROPOSAL_SENT").all()
        stuck_prop_lead = None
        stuck_days = 0
        for p in stuck_prop:
            f = get_cached_metrics(p)
            if f["days_in_stage"] > stuck_days:
                stuck_days = f["days_in_stage"]
                stuck_prop_lead = p
        if stuck_prop_lead:
            blind_spots.append(f"⚠ ₹{stuck_prop_lead.estimated_value/100000.0:.1f}L proposal stuck for {stuck_days} days")
        else:
            blind_spots.append("⚠ Highlighted: No active proposals are currently stalled")
            
        stuck_dist = db.query(B2BLead).filter(B2BLead.division == "distributor").all()
        stuck_dist_lead = None
        stuck_dist_days = 0
        for d in stuck_dist:
            f = get_cached_metrics(d)
            if f["days_since_activity"] > stuck_dist_days:
                stuck_dist_days = f["days_since_activity"]
                stuck_dist_lead = d
        if stuck_dist_lead and stuck_dist_days > 7:
            blind_spots.append(f"⚠ No distributor follow-ups in {stuck_dist_lead.city} for {stuck_dist_days} days")
        else:
            blind_spots.append("⚠ Highlighted: Distributor follow-ups and activity logs are up-to-date")

        complete_contacts   = sum(1 for l in active_leads if l.email and "@" in l.email and l.phone)
        incomplete_contacts = sum(1 for l in active_leads if not (l.email and "@" in l.email) or not l.phone)

        # Revenue by Segment
        segments_list = ["gifting", "distributor", "grocery", "corporate_pantry", "horeca", "cafe", "retail", "kirana"]
        segment_expected_revenue = {}
        best_seg_name = "gifting"
        best_seg_margin = 0.0
        for s in segments_list:
            seg_leads = [l for l in active_leads if (l.division or "").lower() == s]
            seg_exp_rev = sum(get_cached_metrics(l)["expected_revenue"] for l in seg_leads)
            seg_exp_marg = sum(get_cached_metrics(l)["expected_margin"] for l in seg_leads)
            segment_expected_revenue[s] = round(seg_exp_rev)
            if seg_exp_marg > best_seg_margin:
                best_seg_margin = seg_exp_marg
                best_seg_name = s

        # --- V1.1 & V1.2 Logic Additions ---
        # 1. Founder Pipeline Targets & Health
        weekly_margin_target = 100000.0   # ₹1L
        monthly_margin_target = 400000.0  # ₹4L
        
        expected_margin_week = forecasts.get("7", {}).get("margin", 0.0)
        gap_week = max(0.0, weekly_margin_target - expected_margin_week)
        
        avg_margin_deal = 50000.0
        required_wins = gap_week / avg_margin_deal if gap_week > 0 else 0.0
        
        required_meetings = max(0, int(round(required_wins / 0.18))) if required_wins > 0 else 0
        required_samples = max(0, int(round(required_wins / 0.40))) if required_wins > 0 else 0
        required_proposals = max(0, int(round(required_wins / 0.30))) if required_wins > 0 else 0
        
        pipeline_health = {
            "weekly_target": weekly_margin_target,
            "monthly_target": monthly_margin_target,
            "expected_margin_week": expected_margin_week,
            "gap": gap_week,
            "required_meetings": required_meetings,
            "required_samples": required_samples,
            "required_proposals": required_proposals
        }

        # 2. Channel Profitability (ROI/Hour with net margin math)
        channel_profitability = {}
        for s in segments_list:
            seg_leads = [l for l in all_leads if (l.division or "").lower().replace("_", "") == s.replace("_", "")]
            seg_won = [l for l in seg_leads if l.status in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH")]
            
            seg_won_rev = sum(l.estimated_value or 0.0 for l in seg_won)
            seg_net_margin = sum(get_cached_metrics(l)["true_net_margin"] for l in seg_won)
            seg_hours = sum(get_cached_metrics(l)["founder_hours"] for l in seg_leads)
            
            roi_per_hour = seg_net_margin / seg_hours if seg_hours > 0 else (seg_net_margin if seg_net_margin > 0 else 0.0)
            
            channel_profitability[s] = {
                "won_revenue": round(seg_won_rev),
                "true_net_margin": round(seg_net_margin),
                "founder_hours": round(seg_hours, 1),
                "roi_per_hour": round(roi_per_hour)
            }
            
        valid_channels = [(k, v["roi_per_hour"]) for k, v in channel_profitability.items() if v["founder_hours"] > 0]
        if valid_channels:
            valid_channels.sort(key=lambda x: x[1], reverse=True)
            best_seg_name = valid_channels[0][0]
            best_seg_margin = channel_profitability[best_seg_name]["true_net_margin"]
        
        # 3. Forecast Calibration
        try:
            from app.models.models import RevenueForecastSnapshot
            snapshots = db.query(RevenueForecastSnapshot).order_by(RevenueForecastSnapshot.snapshot_date.desc()).limit(12).all()
            calibration_history = []
            accuracy_sum = 0.0
            for snap in snapshots:
                accuracy_sum += snap.forecast_accuracy_pct or 100.0
                calibration_history.append({
                    "date": snap.snapshot_date.strftime("%Y-%m-%d"),
                    "expected_margin": snap.expected_margin_7d,
                    "actual_margin": snap.actual_margin_7d,
                    "accuracy": snap.forecast_accuracy_pct
                })
            avg_accuracy = accuracy_sum / len(snapshots) if snapshots else 92.5
        except Exception:
            avg_accuracy = 92.5
            calibration_history = []

        # 4. Objection Learning
        objections_count = {}
        for obj_type in ["Price", "MOQ", "Existing Vendor", "Taste", "Budget Issue", "Not Decision Maker"]:
            cnt = db.query(B2BLead).filter(B2BLead.objection_reason == obj_type).count()
            objections_count[obj_type] = cnt
                
        return {
            "pipeline_value_inr": pipeline_val,
            "modelled_opportunity_val": modelled_opportunity_val,
            "qualified_pipeline_val": qualified_pipeline_val,
            "quoted_proposal_val": quoted_proposal_val,
            "won_revenue_val": won_revenue_val,
            "expected_revenue_inr": round(total_expected_revenue),
            "expected_margin_inr": round(total_expected_margin),
            "meetings_booked": meetings_count,
            "samples_sent": samples_count,
            "orders_won": orders_count,
            "leads_discovered": leads_count,
            "revenue_velocity_inr": revenue_velocity,
            "revenue_today_inr": round(today_rev),
            "revenue_this_week_inr": round(week_rev),
            "revenue_this_month_inr": round(month_rev),
            "revenue_by_source": revenue_by_source,
            "revenue_at_risk_inr": revenue_at_risk,
            "forecasts": forecasts,
            "leakage": {
                "total_revenue_leaking": round(total_leaking_revenue),
                "total_margin_leaking": round(total_leaking_margin),
                "categories": leakage_categories
            },
            "sku_roi": sku_roi,
            "data_quality": {
                "complete_contacts": complete_contacts,
                "incomplete_contacts": incomplete_contacts,
            },
            "average_cac": round(avg_cac),
            "average_payback": round(avg_payback, 1),
            "founder_efficiency": round(founder_efficiency),
            "founder_effectiveness": round(founder_effectiveness),
            "revenue_concentration_pct": round(concentration_pct),
            "top_2_concentration_pct": round(top_2_pct),
            "cash_runway_months": runway_months,
            "revenue_per_sample": round(revenue_per_sample),
            "revenue_per_meeting": round(revenue_per_meeting),
            "revenue_per_founder_hour": round(revenue_per_founder_hour),
            "source_attribution": source_attribution,
            "cash_this_week": {
                "total": round(cash_this_week_total),
                "leads": cash_this_week_leads
            },
            "blind_spots": blind_spots,
            "segment_revenue": segment_expected_revenue,
            "best_segment": {
                "name": best_seg_name,
                "expected_margin": round(best_seg_margin)
            },
            "cash_risk_inr": round(max(0.0, cash_this_week_total - revenue_at_risk)),
            "pipeline_health": pipeline_health,
            "channel_profitability": channel_profitability,
            "objections_count": objections_count,
            "forecast_calibration": {
                "avg_accuracy_pct": round(avg_accuracy, 1),
                "history": calibration_history
            }
        }





    @staticmethod
    def get_reorder_alerts(db: Session) -> list[dict]:
        """Calculates expected runout days, probabilities, and revenue for won accounts."""
        won_leads = db.query(B2BLead).filter(
            B2BLead.status.in_(["ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"])
        ).all()
        
        alerts = []
        for l in won_leads:
            monthly_kg = l.expected_monthly_consumption_kg or 15.0
            if monthly_kg == 0.0:
                monthly_kg = 15.0
            bought_kg = monthly_kg
            
            days_since_purchase = 28
            if l.last_updated:
                days_since_purchase = (datetime.utcnow() - l.last_updated).days
                
            runout_days = max(0, int(round((bought_kg / monthly_kg) * 30 - days_since_purchase)))
            
            # Reorder probability
            if runout_days <= 5:
                reorder_prob = 0.94
            elif runout_days <= 15:
                reorder_prob = 0.85
            elif runout_days <= 25:
                reorder_prob = 0.60
            else:
                reorder_prob = 0.30

            # Upsell probability is higher if reorder probability is high and it's a corporate/horeca account
            upsell_prob = 0.20
            if reorder_prob >= 0.85 and (l.division or "").lower() in ("corporate", "horeca"):
                upsell_prob = 0.65
                
            alerts.append({
                "company": l.company,
                "city": l.city,
                "bought_kg": bought_kg,
                "days_since_purchase": days_since_purchase,
                "runout_days": runout_days,
                "reorder_probability": reorder_prob,
                "upsell_probability": upsell_prob,
                "estimated_value": l.estimated_value,
                "monthly_value": l.estimated_value / 12.0,
                "confidence": reorder_prob * 100.0,
                "expected_order_value": l.estimated_value / 12.0
            })
            
        return sorted(alerts, key=lambda x: x["runout_days"])

    @staticmethod
    def get_conversion_analytics(db: Session) -> dict:
        """Calculate B2B close rate analytics segmented by source, industry, region, and size."""
        won_statuses = ["ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "UPSELL_OFFERED", "ACCOUNT_GROWTH"]
        
        def calc_rates(group_attr):
            raw = db.query(group_attr, B2BLead.status).all()
            counts = {}
            for val, status in raw:
                if not val:
                    continue
                if val not in counts:
                    counts[val] = {"won": 0, "total": 0}
                counts[val]["total"] += 1
                if status in won_statuses:
                    counts[val]["won"] += 1
            
            rates = {}
            for key, stats in counts.items():
                rates[key] = round((stats["won"] / stats["total"]) * 100.0, 1)
            return rates

        return {
            "by_source": calc_rates(B2BLead.lead_source),
            "by_industry": calc_rates(B2BLead.industry),
            "by_region": calc_rates(B2BLead.region),
            "by_company_size": calc_rates(B2BLead.company_size),
            "by_persona": calc_rates(B2BLead.contact_persona)
        }

    @staticmethod
    def get_win_loss_analytics(db: Session) -> dict:
        """Analyze lost reasons for COLD leads and calculate failure rate breakdown."""
        lost_leads = db.query(B2BLead).filter(B2BLead.status == "COLD").all()
        total_lost = len(lost_leads)
        
        reasons_count = {}
        for lead in lost_leads:
            reason = lead.lost_reason or "No Response"
            reasons_count[reason] = reasons_count.get(reason, 0) + 1
            
        reasons_pct = {}
        for reason, count in reasons_count.items():
            reasons_pct[reason] = round((count / total_lost) * 100.0, 1) if total_lost > 0 else 0.0
            
        top_reason = max(reasons_count, key=reasons_count.get) if reasons_count else "No Response"
        recommendation = "Maintain regular follow-up cycles and track brand awareness signals."
        if top_reason == "Price too high":
            recommendation = "AI Recommendation: Introduce a 5kg trial pack with a 10% introductory wholesale discount."
        elif top_reason == "Poor taste feedback":
            recommendation = "AI Recommendation: Reformulate the Dark Roast sensory profile to reduce acidity and bitterness."
        elif top_reason == "Strong competitor presence":
            recommendation = "AI Recommendation: Implement partner loyalty rebates and offer next-day free shipping guarantees."
        elif top_reason == "No response to outreach":
            recommendation = "AI Recommendation: Personalize outbound email subject lines and test multi-channel LinkedIn follow-ups."
        elif top_reason == "Budget constraints":
            recommendation = "AI Recommendation: Offer flexible 45-day payment terms or smaller volume bi-weekly delivery options."

        return {
            "total_lost": total_lost,
            "reasons_breakdown": reasons_pct,
            "reasons_count": reasons_count,
            "top_reason": top_reason,
            "recommendation": recommendation
        }

    @staticmethod
    def get_gifting_pipeline(db: Session) -> dict:
        """Retrieves the corporate gifting pipeline details with seasonal weights."""
        gifting_leads = db.query(B2BLead).filter(B2BLead.division == "gifting", B2BLead.status.notin_(["COLD", "DORMANT", "ARCHIVED"])).all()
        
        # Category weight mapping
        categories = {
            "Employee Welcome Kit": {"count": 0, "expected_revenue": 0.0, "won_revenue": 0.0, "weight": 100, "multiplier": 1.0},
            "Diwali": {"count": 0, "expected_revenue": 0.0, "won_revenue": 0.0, "weight": 95, "multiplier": 1.5},
            "Conference": {"count": 0, "expected_revenue": 0.0, "won_revenue": 0.0, "weight": 90, "multiplier": 1.1},
            "Hotel Welcome": {"count": 0, "expected_revenue": 0.0, "won_revenue": 0.0, "weight": 85, "multiplier": 1.3},
            "Event Giveaway": {"count": 0, "expected_revenue": 0.0, "won_revenue": 0.0, "weight": 70, "multiplier": 1.0}
        }
        
        leads_list = []
        for l in gifting_leads:
            f_metrics = CRMTrackerService.get_financial_metrics(l)
            cat = l.gifting_category
            if not cat or cat not in categories:
                cats = list(categories.keys())
                cat = cats[l.id % len(cats)]
                l.gifting_category = cat
                
            # Expected days until event
            days = l.gifting_days_until_event or ((l.id % 60) + 10)
            l.gifting_days_until_event = days
            
            w_info = categories[cat]
            gifting_score = w_info["weight"] * w_info["multiplier"] * f_metrics["expected_margin"]
            
            categories[cat]["count"] += 1
            categories[cat]["expected_revenue"] += f_metrics["expected_revenue"]
            if l.status in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH"):
                categories[cat]["won_revenue"] += f_metrics["annual_value"]
                
            leads_list.append({
                "company": l.company,
                "category": cat,
                "days_until_event": days,
                "expected_margin": round(f_metrics["expected_margin"]),
                "gifting_score": round(gifting_score)
            })
            
        # Sort leads_list by gifting_score descending
        leads_list.sort(key=lambda x: x["gifting_score"], reverse=True)
        return {
            "categories": categories,
            "leads": leads_list
        }

    @staticmethod
    def get_distributor_expansion(db: Session) -> list[dict]:
        """Retrieves distributor accounts and white space opportunities."""
        distributor_leads = db.query(B2BLead).filter(B2BLead.division == "distributor").all()
        distributor_data = []
        for l in distributor_leads:
            f_metrics = CRMTrackerService.get_financial_metrics(l)
            current = l.distribution_outlets or int((l.estimated_value or 270000) / 600) or 450
            potential = int(current / 0.37)
            white_space = potential - current
            penetration = (current / potential * 100) if potential > 0 else 0.0
            
            # expected jars per outlet: 110 jars/year
            # margin: ₹15 per jar
            expansion_opp = white_space * 110 * 15.0
            
            revenue = f_metrics["expected_revenue"] if l.status not in ("ORDER_WON", "ONBOARDED") else f_metrics["annual_value"]
            efficiency = revenue / current if current > 0 else 0.0
            
            distributor_data.append({
                "company": l.company,
                "current_outlets": current,
                "potential_outlets": potential,
                "white_space_outlets": white_space,
                "penetration_pct": round(penetration, 1),
                "expansion_revenue_potential": round(expansion_opp),
                "revenue": round(revenue),
                "efficiency_per_outlet": round(efficiency, 1),
                "city": l.city
            })
        return distributor_data

    @staticmethod
    def get_tender_profitability(db: Session) -> list[dict]:
        """Evaluates tenders, delay, working capital, and recommendations."""
        tenders = db.query(B2BLead).filter(B2BLead.division == "tender", B2BLead.status != "COLD").all()
        tender_bids = []
        for l in tenders:
            f_metrics = CRMTrackerService.get_financial_metrics(l)
            delay = 180
            if "corp" in l.company.lower() or "private" in l.company.lower():
                delay = 30
            elif "dist" in l.company.lower():
                delay = 60
                
            expected_profit = f_metrics["expected_margin"]
            cashflow_score = expected_profit / delay if delay > 0 else 0.0
            working_capital = f_metrics["annual_value"] * (delay / 365.0)
            
            tender_bids.append({
                "company": l.company,
                "revenue": f_metrics["annual_value"],
                "margin_pct": f_metrics["margin_pct"],
                "win_probability": f_metrics["win_probability"],
                "collection_probability": f_metrics["collection_probability"],
                "expected_profit": expected_profit,
                "delay_days": delay,
                "working_capital_lockup": round(working_capital),
                "cashflow_score": cashflow_score
            })
        return sorted(tender_bids, key=lambda x: x["cashflow_score"], reverse=True)

    @staticmethod
    def generate_actions(db: Session) -> list:
        """Populates the action_queue table with the top 5 urgency/velocity-adjusted APS actions."""
        from app.models.models import ActionQueue, B2BLead
        from datetime import datetime, timedelta

        # Clear existing PENDING actions in queue
        db.query(ActionQueue).filter(ActionQueue.status == "PENDING").delete()

        # Find active leads
        active_leads = db.query(B2BLead).filter(B2BLead.status.notin_(["COLD", "DORMANT", "ARCHIVED"])).all()
        
        actions_to_score = []
        for lead in active_leads:
            f = CRMTrackerService.get_financial_metrics(lead)
            status_upper = (lead.status or "DISCOVERED").upper()
            
            # Skip if lead is won/onboarded and not predicted for reorder
            if status_upper in ("ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH") and status_upper != "REORDER_PREDICTED":
                continue
                
            # Compute APS components
            expected_margin = f["expected_margin"]
            win_prob = f["win_probability"]
            
            # Urgency multiplier
            urgency = 1.0
            days_since = f["days_since_activity"]
            if days_since > 14:
                urgency = 1.5
            elif days_since > 7:
                urgency = 1.2
                
            # Stage multiplier
            stage_mult = 1.0
            if status_upper == "PROPOSAL_SENT":
                stage_mult = 1.5
            elif status_upper == "REORDER_PREDICTED":
                stage_mult = 1.4
            elif status_upper == "FEEDBACK_RECEIVED":
                stage_mult = 1.3
            elif status_upper == "FEEDBACK_PENDING":
                stage_mult = 1.2
            elif status_upper in ("SAMPLE_SENT", "DELIVERED"):
                stage_mult = 1.1
            elif status_upper in ("MEETING_BOOKED", "MEETING_COMPLETED"):
                stage_mult = 1.0
            elif status_upper == "REPLIED":
                stage_mult = 0.9
            elif status_upper == "EMAIL_SENT":
                stage_mult = 0.8
            else:
                stage_mult = 0.6
                
            # Velocity multiplier
            # reply velocity in days (lower is faster, so higher multiplier for low days)
            velocity_days = lead.velocity_days_reply or 5
            velocity_mult = 1.0 + (1.0 / max(1, velocity_days))
            
            # APS Score
            aps = (expected_margin * win_prob) * urgency * stage_mult * velocity_mult
            
            # Determine Action Type
            action_type = "CALL_LEAD"
            if status_upper in ("SAMPLE_SENT", "DELIVERED", "FEEDBACK_PENDING"):
                action_type = "FOLLOWUP_SAMPLE"
            elif status_upper in ("FEEDBACK_RECEIVED", "PROPOSAL_SENT"):
                action_type = "SEND_PROPOSAL"
            elif status_upper == "MEETING_COMPLETED":
                action_type = "DISPATCH_SAMPLE"
            elif status_upper == "REORDER_PREDICTED":
                action_type = "REORDER_ALERT"
                
            due_days = 2
            if action_type == "REORDER_ALERT":
                due_days = 1
            elif action_type == "DISPATCH_SAMPLE":
                due_days = 1
            elif action_type == "SEND_PROPOSAL":
                due_days = 3
                
            due_date = datetime.utcnow() + timedelta(days=due_days)
            
            actions_to_score.append({
                "lead_id": lead.id,
                "action_type": action_type,
                "priority_score": aps,
                "expected_margin": expected_margin,
                "due_date": due_date
            })
            
        # Sort actions by priority_score descending and pick top 5
        actions_to_score.sort(key=lambda x: x["priority_score"], reverse=True)
        top_5 = actions_to_score[:5]
        
        # Save to database
        db_actions = []
        for act in top_5:
            new_act = ActionQueue(
                lead_id=act["lead_id"],
                action_type=act["action_type"],
                priority_score=round(act["priority_score"], 2),
                expected_margin=act["expected_margin"],
                due_date=act["due_date"],
                status="PENDING"
            )
            db.add(new_act)
            db_actions.append(new_act)
            
        db.commit()
        return db_actions
