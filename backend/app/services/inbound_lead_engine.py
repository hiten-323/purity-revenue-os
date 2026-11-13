"""
High-Speed Inbound Lead Acquisition & Multi-Touch Conversion Engine for Purity Revenue OS V2.0.
Inspired by Bashar Jkatou's rapid-response AI automation system (<60 second speed-to-lead).

Capabilities:
1. Inbound Webhook: Ingests leads from website (p3online.in), Instagram/Meta Ads, WhatsApp,
   and Shopify inquiries with sub-60-second response latency.
2. Real-time Product Fit: Automatically maps prospect needs to Purica, Purista, Bold,
   Ultra Blend, or Gourmet Duo Mini COD sample pack.
3. Multi-Touch Cadence Scheduler:
   - Touch 0 (Immediate <60s): Personalized welcome + product recommendation + 1-click COD link.
   - Touch 1 (+15m): Packaging format check (50g jars vs single-serve sachets).
   - Touch 2 (+2h): Taste guarantee & factory-direct quality pledge.
   - Touch 3 (+24h): Wholesale pricing slabs & GST input credit.
4. Persistent Memory: Extracts preferences and logs long-term memories via PersistentMemoryEngine.
5. Paperclip Governance: Enforces budget limits and founder approvals for bulk terms.
"""
from __future__ import annotations

import logging
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

from app.services.product_fit_engine import ProductFitEngine, PRODUCT_CATALOGUE
from app.services.persistent_memory import PersistentMemoryEngine
from app.services.agent_governance import AgentGovernanceEngine, WHATSAPP_COST_PER_MSG_INR

_log = logging.getLogger("inbound_lead_engine")


@dataclass
class InboundLeadPayload:
    name: str
    phone: str
    email: Optional[str] = None
    company: Optional[str] = None
    city: Optional[str] = None
    source: str = "website_form"
    inquiry_text: str = ""
    preferred_format: Optional[str] = None
    business_type: Optional[str] = None
    estimated_monthly_kg: Optional[float] = None
    consent_explicit: bool = False
    consent_text: Optional[str] = None
    consent_channels: List[str] = field(default_factory=list)
    consent_evidence_url: Optional[str] = None
    consent_signature_valid: bool = False


@dataclass
class CadenceStep:
    step_number: int
    delay_minutes: int
    channel: str
    subject: str
    message: str
    cta_link: str
    scheduled_for: str


@dataclass
class InboundProcessResult:
    lead_id: int
    company: str
    recommended_product: str
    recommended_sku: str
    direct_checkout_link: str
    immediate_message_draft: str
    cadence: List[CadenceStep]
    memory_extracted: List[str]
    governance_approved: bool
    status: str


class InboundSpeedToLeadEngine:
    """
    Sub-60-second speed-to-lead acquisition and multi-touch follow-up engine.
    """

    @classmethod
    def clean_phone(cls, phone_raw: str) -> str:
        digits = re.sub(r"\D", "", phone_raw or "")
        if digits.startswith("91") and len(digits) == 12:
            return digits[2:]
        return digits[-10:] if len(digits) >= 10 else digits

    @classmethod
    def _valid_consent_channels(cls, payload: InboundLeadPayload) -> set[str]:
        if not payload.consent_signature_valid or not payload.consent_explicit:
            return set()
        text = (payload.consent_text or "").strip().lower()
        if len(text) < 20:
            return set()
        aliases = {"call": "voice", "phone": "voice", "voice_call": "voice", "wa": "whatsapp"}
        requested = {aliases.get(str(x).strip().lower(), str(x).strip().lower()) for x in (payload.consent_channels or [])}
        terms = {
            "voice": ("call", "phone", "telephone", "voice"),
            "whatsapp": ("whatsapp",),
            "email": ("email",),
        }
        return {
            channel for channel in requested
            if channel in terms and any(term in text for term in terms[channel])
        }

    @classmethod
    def _record_consent_events(cls, lead, payload: InboundLeadPayload, channels: set[str],
                               cleaned_phone: str, now: datetime, db: Session) -> None:
        if not channels:
            return
        from app.models.models import WorkflowEvent
        event_types = {
            "voice": "CALL_AUTHORIZATION_RECORDED",
            "whatsapp": "WHATSAPP_CONSENT_RECORDED",
            "email": "EMAIL_AUTHORIZATION_RECORDED",
        }
        email = (payload.email or getattr(lead, "email", None) or "").strip().lower()
        text = (payload.consent_text or "").strip()
        for channel in sorted(channels):
            destination = email if channel == "email" else cleaned_phone
            if not destination:
                continue
            evidence = "|".join((
                str(lead.id), channel, payload.source or "unknown",
                payload.consent_evidence_url or "", now.isoformat(), destination, text,
            ))
            digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
            db.add(WorkflowEvent(
                event_type=event_types[channel],
                lead_id=lead.id,
                actor="INBOUND_WEBHOOK",
                channel=channel,
                payload={
                    "source": payload.source,
                    "consent_channel": channel,
                    "consent_text": text,
                    "consent_phone": cleaned_phone if channel in {"voice", "whatsapp"} else None,
                    "consent_email": email if channel == "email" else None,
                    "evidence_sha256": digest,
                    "source_url": payload.consent_evidence_url,
                    "captured_at": now.isoformat(),
                    "explicit": True,
                },
                occurred_at=now,
            ))

    @classmethod
    def process_inbound_lead(cls, payload: InboundLeadPayload, db: Session) -> InboundProcessResult:
        """
        Executes complete sub-60-second intake, fit matching, memory extraction,
        cadence creation, and dispatch preparation.
        """
        from app.models.models import B2BLead, LeadInteraction, WorkflowEvent

        cleaned_phone = cls.clean_phone(payload.phone)
        consent_channels = cls._valid_consent_channels(payload)
        consent_valid = bool(consent_channels)
        company_name = (payload.company or payload.name or "Coffee Buyer").strip()

        # 1. Deduplicate / Upsert Lead Record
        lead = None
        if cleaned_phone:
            lead = db.query(B2BLead).filter(B2BLead.phone == cleaned_phone).first()
        if not lead and payload.email:
            lead = db.query(B2BLead).filter(B2BLead.email == payload.email.lower().strip()).first()
        if not lead and company_name:
            lead = db.query(B2BLead).filter(B2BLead.company == company_name).first()

        now = datetime.utcnow()
        if not lead:
            # Check company uniqueness
            existing_comp = db.query(B2BLead).filter(B2BLead.company == company_name).first()
            if existing_comp:
                company_name = f"{company_name} ({cleaned_phone[-4:] if cleaned_phone else 'Inbound'})"

            lead = B2BLead(
                company=company_name,
                contact_name=payload.name,
                phone=cleaned_phone,
                whatsapp_number=cleaned_phone,
                email=payload.email.lower().strip() if payload.email else None,
                city=payload.city or "India",
                lead_source=f"inbound_{payload.source}",
                status="INBOUND_INTEREST",
                priority="HIGH",
                score=95,
                intent_score=95,
                intent_tier="PRIORITY_A",
                contact_status="NEEDS_RESEARCH",
                contact_status_reason="Inbound form received; independently verify contact destination and channel-specific consent before outreach.",
                consent_status="EXPLICIT" if consent_valid else "UNKNOWN",
                consent_source=payload.source if consent_valid else None,
                consent_timestamp=now if consent_valid else None,
                consent_phone=cleaned_phone if consent_valid and consent_channels.intersection({"voice", "whatsapp"}) else None,
                whatsapp_verified=False,
                phone_verified=False,
                email_verified=False,
                phone_trust="DISCOVERED" if cleaned_phone else "UNKNOWN",
                email_trust="DISCOVERED" if payload.email else "UNKNOWN",
                qualification_notes=f"Inbound inquiry: {payload.inquiry_text[:200]}",
                coffee_buying_evidence=f"Direct inbound enquiry via {payload.source} requesting coffee information.",
                coffee_buying_score=95,
                stage_entered_date=now
            )
            db.add(lead)
            db.commit()
            db.refresh(lead)
            _log.info("New inbound lead created: id=%d company=%s phone=%s", lead.id, lead.company, lead.phone)
        else:
            # An inbound form is not phone verification and does not grant
            # every channel consent by implication.
            if consent_valid:
                lead.consent_status = "EXPLICIT"
                lead.consent_source = payload.source
                lead.consent_timestamp = now
                lead.consent_phone = cleaned_phone if consent_channels.intersection({"voice", "whatsapp"}) else None
            else:
                existing_events = db.query(WorkflowEvent).filter(
                    WorkflowEvent.lead_id == lead.id,
                    WorkflowEvent.event_type.in_(("CALL_AUTHORIZATION_RECORDED", "WHATSAPP_CONSENT_RECORDED", "EMAIL_AUTHORIZATION_RECORDED")),
                ).count()
                if not existing_events:
                    lead.consent_status = "UNKNOWN"
            if not getattr(lead, "phone_verified_at", None) and (getattr(lead, "phone_trust", "") or "").upper() not in {"VERIFIED", "FOUNDER_VERIFIED"}:
                lead.phone_verified = False
                if lead.phone:
                    lead.phone_trust = "DISCOVERED"
            if not getattr(lead, "whatsapp_verified_at", None):
                lead.whatsapp_verified = False
            if not getattr(lead, "email_verified_at", None) and (getattr(lead, "email_trust", "") or "").upper() not in {"VERIFIED", "FOUNDER_VERIFIED", "REPLIED"}:
                lead.email_verified = False
                if lead.email:
                    lead.email_trust = "DISCOVERED"
            if (getattr(lead, "contact_status", "") or "").upper() not in {"DO_NOT_CONTACT", "OPTED_OUT", "SUPPRESSED", "QUARANTINED"}:
                lead.contact_status = "NEEDS_RESEARCH"
                lead.contact_status_reason = "Inbound form received; independently verify contact destination and channel-specific consent before outreach."
            lead.intent_score = max(lead.intent_score or 0, 95)
            lead.intent_tier = "PRIORITY_A"
            lead.qualification_notes = (lead.qualification_notes or "") + f" | Recent inbound ({now.strftime('%d-%b')}): {payload.inquiry_text[:150]}"
            db.commit()
            _log.info("Existing lead updated from inbound: id=%d company=%s", lead.id, lead.company)

        if consent_valid:
            cls._record_consent_events(lead, payload, consent_channels, cleaned_phone, now, db)
            db.commit()

        # 2. Extract and Store Persistent Long-Term Memory (claude-mem style)
        extracted_facts = PersistentMemoryEngine.extract_and_store_memories(
            lead_id=lead.id,
            raw_text=f"{payload.inquiry_text} format={payload.preferred_format} type={payload.business_type}",
            interaction_method=f"inbound_{payload.source}",
            db=db,
            actor="INBOUND_SPEED_AI"
        )
        fact_summaries = [f"{f.category}: {f.value}" for f in extracted_facts]

        # B2B enquiries belong to the wholesale sales pipeline, not the D2C
        # Shopify/COD funnel. Capture intent and route to founder review only.
        is_b2b = bool(payload.company) or bool(payload.estimated_monthly_kg) or (
            bool(payload.business_type) and payload.business_type.strip().lower() not in {"consumer", "individual"}
        )
        if is_b2b:
            volume = f"{payload.estimated_monthly_kg:g} kg/month" if payload.estimated_monthly_kg else "not stated"
            internal_note = (
                f"B2B INBOUND REVIEW - {company_name}. Business type: {payload.business_type or 'not stated'}. "
                f"Estimated volume: {volume}. Format: {payload.preferred_format or 'not stated'}. "
                f"Source: {payload.source}. Consent: {lead.consent_status}. "
                "No outbound message or call has been sent. Verify the contact and channel permissions, "
                "then use the approved B2B pricebook/MOQ and sample terms to prepare a quotation."
            )
            db.add(LeadInteraction(
                lead_id=lead.id,
                created_by="INBOUND_SPEED_AI",
                method="inbound_webhook",
                outcome="INBOUND_INTEREST_CAPTURED",
                remark=internal_note,
                occurred_at=now,
                created_at=now,
            ))
            db.add(WorkflowEvent(
                event_type="B2B_INBOUND_REVIEW_REQUIRED",
                lead_id=lead.id,
                actor="SYSTEM",
                channel="inbound",
                payload={
                    "source": payload.source,
                    "business_type": payload.business_type,
                    "estimated_monthly_kg": payload.estimated_monthly_kg,
                    "preferred_format": payload.preferred_format,
                    "consent_status": lead.consent_status,
                    "consent_channels_recorded": sorted(consent_channels),
                    "outbound_dispatched": False,
                    "reason": "B2B pricebook and independent channel verification required",
                },
                occurred_at=now,
            ))
            db.commit()
            return InboundProcessResult(
                lead_id=lead.id,
                company=lead.company,
                recommended_product="B2B_PRICEBOOK_PENDING",
                recommended_sku="",
                direct_checkout_link="",
                immediate_message_draft=internal_note,
                cadence=[],
                memory_extracted=fact_summaries,
                governance_approved=False,
                status="B2B_REVIEW_REQUIRED",
            )

        # 3. Product Fit Matching (via authoritative Shopify catalogue)
        inq_lower = (payload.inquiry_text or "").lower()
        taste_pref = "Arabica" if "arabica" in inq_lower else None
        strength_pref = "Strong" if ("strong" in inq_lower or "robusta" in inq_lower) else None
        chicory_ok = True if "chicory" in inq_lower and "no chicory" not in inq_lower and "zero chicory" not in inq_lower else False
        price_sens = "high" if any(w in inq_lower for w in ["budget", "cheap", "cost", "inexpensive"]) else "medium"

        product_fit = ProductFitEngine.recommend(
            taste_preference=taste_pref,
            strength_preference=strength_pref,
            chicory_allowed=chicory_ok,
            price_sensitivity=price_sens,
            business_type=payload.business_type or "cafe",
            is_first_order=True
        )
        primary_sku = product_fit.primary_sku
        primary_product = product_fit.primary_product
        unit_mrp = product_fit.mrp
        format_desc = product_fit.format
        checkout_link = product_fit.checkout_url or "https://www.p3online.in/products/gourmet-duo-mini"

        # 4. Generate Multi-Touch Conversational Cadence
        first_name = (payload.name or "there").split()[0].title()
        comp_display = company_name

        # Touch 0: Immediate (<60s)
        msg_touch0 = (
            f"Hello {first_name}! This is Purity Beans (p3online.in). "
            f"Thank you for contacting us regarding instant coffee for {comp_display}.\n\n"
            f"Based on your requirements, we recommend our {primary_product} "
            f"({format_desc}, MRP ₹{unit_mrp:,.0f}). "
            f"It offers direct factory sourcing with zero middlemen margins.\n\n"
            f"Would you like us to dispatch our Gourmet Duo Tasting Kit (Purica Arabica + Purista Robusta) "
            f"directly to your doorstep via Cash on Delivery (COD)?\n\n"
            f"Order in 1 click here: {checkout_link}\n"
            f"Or reply 'YES' to confirm dispatch address!"
        )

        # Touch 1: +15 minutes
        msg_touch1 = (
            f"Hi {first_name}, one quick question regarding {comp_display}: "
            f"Do you prefer 50g glass jars for shelf presentation, or individual single-serve sachets? "
            f"We pack both formats directly at our facility."
        )

        # Touch 2: +2 hours
        msg_touch2 = (
            f"Hi {first_name}, we know trying a new coffee brand can be uncertain. "
            f"Every Purity Beans batch comes with our factory-fresh roast guarantee and 100% pure coffee pledge. "
            f"Would you like us to send a 2-jar sample box via COD today so you can taste the difference before ordering bulk?"
        )

        # Touch 3: +24 hours
        msg_touch3 = (
            f"Hello {first_name}, following up from Purity Beans. "
            f"For commercial & wholesale orders, we provide full GST input tax credit invoices "
            f"plus direct wholesale slabs (up to 28.75% margin for partner accounts). "
            f"Let us know if you'd like our formal commercial price sheet!"
        )

        cadence = [
            CadenceStep(
                step_number=0,
                delay_minutes=0,
                channel="whatsapp",
                subject="Purity Beans Coffee Inquiry",
                message=msg_touch0,
                cta_link=checkout_link,
                scheduled_for=now.isoformat()
            ),
            CadenceStep(
                step_number=1,
                delay_minutes=15,
                channel="whatsapp",
                subject="Packaging Preference",
                message=msg_touch1,
                cta_link=checkout_link,
                scheduled_for=(now + timedelta(minutes=15)).isoformat()
            ),
            CadenceStep(
                step_number=2,
                delay_minutes=120,
                channel="whatsapp",
                subject="Taste Guarantee Sample",
                message=msg_touch2,
                cta_link=checkout_link,
                scheduled_for=(now + timedelta(hours=2)).isoformat()
            ),
            CadenceStep(
                step_number=3,
                delay_minutes=1440,
                channel="email",
                subject=f"Wholesale Pricing & GST Invoicing for {comp_display}",
                message=msg_touch3,
                cta_link=checkout_link,
                scheduled_for=(now + timedelta(days=1)).isoformat()
            )
        ]

        # Only channels with signed, channel-specific consent may enter a cadence.
        cadence = [
            step for step in cadence
            if (step.get("channel") if isinstance(step, dict) else getattr(step, "channel", "")) in consent_channels
        ]

        # 5. Governance Evaluation (Paperclip)
        gov_check = AgentGovernanceEngine.check_spend_allowed(WHATSAPP_COST_PER_MSG_INR, db) if cadence else None

        # Log lead interaction
        ix = LeadInteraction(
            lead_id=lead.id,
            created_by="INBOUND_SPEED_AI",
            method="inbound_webhook",
            outcome="CADENCE_GENERATED" if cadence else "INBOUND_INTEREST_CAPTURED",
            remark=f"Inbound lead processed. Recommended: {primary_product}. Consent-gated cadence: {len(cadence)} steps.",
            occurred_at=now,
            created_at=now
        )
        db.add(ix)
        db.commit()

        return InboundProcessResult(
            lead_id=lead.id,
            company=lead.company,
            recommended_product=primary_product,
            recommended_sku=primary_sku,
            direct_checkout_link=checkout_link,
            immediate_message_draft=msg_touch0,
            cadence=cadence,
            memory_extracted=fact_summaries,
            governance_approved=gov_check,
            status="SUCCESS_SPEED_CADENCE_READY" if cadence else "CONSENT_REVIEW_REQUIRED"
        )
