"""
Customer Segmentation Engine for Purity Revenue OS V2.0.
Categorizes leads into 8 distinct strategic segments with tailored value propositions,
outreach channel priorities, and conversion workflows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

_log = logging.getLogger("customer_segmentation")

# 8 Strategic Segments
SEGMENT_ACTIVE_ONLINE_BUYER = "ACTIVE_ONLINE_BUYER"
SEGMENT_HORECA = "HORECA"
SEGMENT_DISTRIBUTOR = "DISTRIBUTOR_WHOLESALER"
SEGMENT_RETAILER = "RETAILER_SUPERMARKET"
SEGMENT_CORPORATE = "CORPORATE_OFFICE"
SEGMENT_CONSUMER = "INDIVIDUAL_CONSUMER"
SEGMENT_COMPETITOR_SWITCH = "COMPETITOR_SWITCH"
SEGMENT_GENERIC_DIRECTORY = "GENERIC_DIRECTORY"

ALL_SEGMENTS = [
    SEGMENT_ACTIVE_ONLINE_BUYER,
    SEGMENT_HORECA,
    SEGMENT_DISTRIBUTOR,
    SEGMENT_RETAILER,
    SEGMENT_CORPORATE,
    SEGMENT_CONSUMER,
    SEGMENT_COMPETITOR_SWITCH,
    SEGMENT_GENERIC_DIRECTORY,
]

@dataclass
class SegmentProfile:
    code: str
    name: str
    description: str
    primary_channels: List[str]
    value_proposition: str
    recommended_sku_types: List[str]
    qualification_priority: str  # "HIGH", "MEDIUM", "LOW", "HOLD"
    starter_offer: str


SEGMENT_METADATA: Dict[str, SegmentProfile] = {
    SEGMENT_ACTIVE_ONLINE_BUYER: SegmentProfile(
        code=SEGMENT_ACTIVE_ONLINE_BUYER,
        name="Active Online Coffee Buyer",
        description="Prospective buyers actively searching online or submitting inbound enquiries for coffee.",
        primary_channels=["PHONE", "EMAIL", "WHATSAPP_CONSENTED"],
        value_proposition="Freshly freeze-dried pure coffee granules delivered directly with COD & free shipping.",
        recommended_sku_types=["PB-GOURMET-DUO-50", "PB-VARIETY-4x50", "PB-PCA-100", "PB-PST-100"],
        qualification_priority="HIGH",
        starter_offer="Gourmet Duo Mini (₹570) or Variety Box (₹877)"
    ),
    SEGMENT_HORECA: SegmentProfile(
        code=SEGMENT_HORECA,
        name="HoReCa (Cafe / Restaurant / Hotel / Caterer)",
        description="Hospitality food & beverage operators requiring consistent coffee quality and rich crema.",
        primary_channels=["PHONE", "EMAIL"],
        value_proposition="Superior freeze-dried aroma and crema for premium beverage service without expensive espresso machine overhead.",
        recommended_sku_types=["PB-PST-100", "PB-PCA-100", "PB-VARIETY-4x50"],
        qualification_priority="HIGH",
        starter_offer="Commercial Tasting Variety Box (₹877) or Gourmet Duo Mini"
    ),
    SEGMENT_DISTRIBUTOR: SegmentProfile(
        code=SEGMENT_DISTRIBUTOR,
        name="Distributor & Wholesaler",
        description="FMCG and beverage distribution partners seeking attractive margins and reliable supply.",
        primary_channels=["PHONE", "EMAIL"],
        value_proposition="Direct factory supply with 30% margin structure (25% trade + 5% cash discount) and GST invoicing.",
        recommended_sku_types=["MASTER_CARTON_PB-BLD-100", "MASTER_CARTON_PB-PST-100", "PB-VARIETY-4x50"],
        qualification_priority="HIGH",
        starter_offer="Wholesale Starter Carton with Distributor Margin"
    ),
    SEGMENT_RETAILER: SegmentProfile(
        code=SEGMENT_RETAILER,
        name="Retailer & Supermarket",
        description="Independent grocery, gourmet marts, and retail stores with premium shelf displays.",
        primary_channels=["PHONE", "EMAIL"],
        value_proposition="High-margin embossed glass jars with modern aesthetic and barcode/FSSAI compliance.",
        recommended_sku_types=["PB-PCA-50", "PB-PST-50", "PB-BLD-50", "PB-VARIETY-4x50"],
        qualification_priority="MEDIUM",
        starter_offer="Assorted 24-Jar Retail Trial Pack"
    ),
    SEGMENT_CORPORATE: SegmentProfile(
        code=SEGMENT_CORPORATE,
        name="Corporate & Office Procurement",
        description="Admin, HR, and facility teams managing office pantry and executive coffee supplies.",
        primary_channels=["EMAIL", "PHONE"],
        value_proposition="Premium office pantry coffee with 100% GST input tax credit and automated monthly replenishment.",
        recommended_sku_types=["PB-BLD-100", "PB-PCA-100", "PB-GOURMET-DUO-100"],
        qualification_priority="HIGH",
        starter_offer="Office Pantry Trial Pack with GST Invoice"
    ),
    SEGMENT_CONSUMER: SegmentProfile(
        code=SEGMENT_CONSUMER,
        name="Individual Online Consumer",
        description="Direct coffee enthusiasts seeking premium taste upgrades at home.",
        primary_channels=["EMAIL", "WHATSAPP_CONSENTED"],
        value_proposition="100% pure freeze-dried coffee granules delivered to your door with COD.",
        recommended_sku_types=["PB-GOURMET-DUO-50", "PB-PCA-50", "PB-PST-50"],
        qualification_priority="MEDIUM",
        starter_offer="Gourmet Duo Mini (₹570) with Cash on Delivery"
    ),
    SEGMENT_COMPETITOR_SWITCH: SegmentProfile(
        code=SEGMENT_COMPETITOR_SWITCH,
        name="Competitor Switch Prospect",
        description="Businesses or buyers currently using Nescafe, Bru, Tata Coffee, or Continental.",
        primary_channels=["PHONE", "EMAIL"],
        value_proposition="Direct factory freeze-dried coffee: rich caramel notes without the bitter burnt taste of mass-market brands.",
        recommended_sku_types=["PB-PCA-50", "PB-PST-50", "PB-BLD-50"],
        qualification_priority="HIGH",
        starter_offer="Taste Comparison Starter Duo"
    ),
    SEGMENT_GENERIC_DIRECTORY: SegmentProfile(
        code=SEGMENT_GENERIC_DIRECTORY,
        name="Generic Directory Contact",
        description="Directory entries without confirmed coffee buying intent or verified contact data.",
        primary_channels=["HOLD"],
        value_proposition="Awaiting qualification.",
        recommended_sku_types=[],
        qualification_priority="HOLD",
        starter_offer="None until enriched"
    )
}


class CustomerSegmentationEngine:
    """
    Evaluates lead data, evidence signals, business category, and intent to classify
    prospects into the 8 authoritative customer segments.
    """

    @classmethod
    def classify(cls, lead: Any, evidence_list: Optional[List[Any]] = None) -> SegmentProfile:
        """
        Classifies a lead into one of the 8 segments.
        """
        phone = getattr(lead, "phone", None)
        email = getattr(lead, "email", None)
        confidence = float(getattr(lead, "contact_confidence", 0.0) or 0.0)

        # Gate 0: If missing both phone and email, or negligible confidence without category, HOLD
        if not phone and not email:
            return SEGMENT_METADATA[SEGMENT_GENERIC_DIRECTORY]

        category = str(getattr(lead, "category", "") or "").lower()
        company = str(getattr(lead, "company", "") or "").lower()
        title = str(getattr(lead, "decision_maker_title", "") or "").lower()
        intent_type = str(getattr(lead, "intent_type", "") or "").lower()
        notes = str(getattr(lead, "notes", "") or "").lower()
        website = str(getattr(lead, "website", "") or "").lower()

        # Combine evidence texts if available
        evidence_texts = []
        if evidence_list:
            for ev in evidence_list:
                evidence_texts.append(str(getattr(ev, "evidence_text", "") or "").lower())
                evidence_texts.append(str(getattr(ev, "signal_type", "") or "").lower())
        all_text = " ".join([category, company, title, intent_type, notes, website] + evidence_texts)

        # 1. Active Online Buying Enquiry (Highest Priority)
        if any(k in all_text for k in [
            "enquiry", "online inquiry", "inbound", "looking for coffee",
            "coffee requirement", "procurement request", "sample request",
            "quotation request", "buy instant coffee"
        ]):
            return SEGMENT_METADATA[SEGMENT_ACTIVE_ONLINE_BUYER]

        # 2. Competitor Switch (Nescafe, Bru, Tata, Continental, Davidoff)
        if any(comp in all_text for comp in ["nescafe", "bru coffee", "tata coffee", "continental coffee", "davidoff"]):
            return SEGMENT_METADATA[SEGMENT_COMPETITOR_SWITCH]

        # 3. Distributor / Wholesaler
        if any(k in all_text for k in ["distributor", "wholesale", "wholesaler", "dealership", "fmcg trade", "stockist"]):
            return SEGMENT_METADATA[SEGMENT_DISTRIBUTOR]

        # 4. HoReCa (Cafe, Restaurant, Hotel, Caterer, Barista, Resort)
        if any(k in all_text for k in ["cafe", "coffee shop", "restaurant", "hotel", "resort", "caterer", "catering", "bistro", "bakery", "hospitality"]):
            return SEGMENT_METADATA[SEGMENT_HORECA]

        # 5. Corporate & Office Procurement
        if any(k in all_text for k in ["office", "corporate", "pantry", "admin", "hr", "facility", "it park", "workplace", "tech park"]):
            return SEGMENT_METADATA[SEGMENT_CORPORATE]

        # 6. Retailer & Supermarket
        if any(k in all_text for k in ["supermarket", "grocery", "retail", "kirana", "gourmet store", "mart", "store"]):
            return SEGMENT_METADATA[SEGMENT_RETAILER]

        # 7. Individual Online Consumer
        if any(k in all_text for k in ["consumer", "individual", "personal", "home coffee", "coffee lover"]):
            return SEGMENT_METADATA[SEGMENT_CONSUMER]

        # Low confidence fallback
        if confidence < 0.40 and not any(k in all_text for k in ["coffee", "beverage", "pantry", "food"]):
            return SEGMENT_METADATA[SEGMENT_GENERIC_DIRECTORY]

        # Default fallback if valid contact but unspecific category
        if "corporate" in category or "business" in category:
            return SEGMENT_METADATA[SEGMENT_CORPORATE]

        return SEGMENT_METADATA[SEGMENT_HORECA] if "food" in category else SEGMENT_METADATA[SEGMENT_ACTIVE_ONLINE_BUYER]

    @classmethod
    def sweep_and_tag_segments(cls, db: Session) -> Dict[str, int]:
        """
        Sweeps database and returns breakdown of leads across the 8 segments.
        """
        from app.models.models import B2BLead
        leads = db.query(B2BLead).all()
        counts: Dict[str, int] = {seg: 0 for seg in ALL_SEGMENTS}
        for lead in leads:
            profile = cls.classify(lead)
            counts[profile.code] += 1
        return counts
