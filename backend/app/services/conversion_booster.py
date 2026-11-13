"""
Conversion Booster Engine for Purity Beans B2B Revenue OS.

Provides:
1. Daily Hot 20 Lead Prioritization (ranking leads by conversion probability).
2. Category-Specific Wedge Offers (lowering commitment barrier to secure daily conversions).
3. Battle-tested Scripts and Follow-Up Playbooks across all channels & scenarios.
4. SLA Escalation & Conversion Accelerators.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.high_intent_scorer import evaluate_intent, IntentEvaluation
from app.services.contact_eligibility import evaluate_lead_eligibility

log = logging.getLogger("conversion_booster")


@dataclass
class HotLeadCandidate:
    lead_id: int
    company_name: str
    contact_name: str
    phone: str
    email: str
    city: str
    category: str
    intent_score: int
    recommended_channel: str
    wedge_offer: str
    action_item: str


# ── Scenario Script Playbook ──────────────────────────────────────────────────

SCENARIO_SCRIPTS: Dict[str, Dict[str, Any]] = {
    "COLD_INTRODUCTION": {
        "description": "First outreach to a new qualified buyer.",
        "ai_call": {
            "opening": "Namaste {name} ji, kya main 2 minute baat kar sakta hun? Main Hiten Jain bol raha hun Purity Beans coffee se.",
            "hook": "Hum {city} ke top cafes aur retail partners ko 100% pure instant coffee provide kar rahe hain — direct factory rates par.",
            "value_prop": "Chicory-free premium blend hai jo counter par direct sell hota hai. Distributor aur cafe partners ke liye direct 30% margin hai.",
            "cta": "Kya main WhatsApp par hamara 12-Jar Starter Trial Carton details share kar sakta hun?",
        },
        "whatsapp": (
            "Namaste {name} ji 🙏\n\n"
            "Main Hiten Jain hun — Purity Beans (Pure Pantry Provisions) ka Founder.\n"
            "{city} mein aapke business ke liye hamari 100% Pure Instant Coffee (Zero Chicory) ka rate list share kar raha hun.\n\n"
            "🔥 Cafe & Retailers Special:\n"
            "• Direct Factory Rates (30% Gross Margin)\n"
            "• ₹4.20 per cup cost vs ₹18-₹25 selling price\n"
            "• 12-Jar Starter Trial Carton available (No heavy commitment)\n\n"
            "Kya main sample details bhej sakta hun?\n"
            "📞 +91 90849 58495"
        ),
        "email_subject": "Wholesale Coffee Supply & Starter Carton Trial — Purity Beans",
    },
    "NO_ANSWER_OR_BUSY": {
        "description": "Lead did not pick up or line was busy (Bounded retry).",
        "ai_call": {
            "note": "Log NO_ANSWER. Bounded retry scheduled for 4 hours later within 09:30-19:30 IST window. Max 3 attempts.",
        },
        "whatsapp": (
            "Namaste {name} ji, Purity Beans coffee se Hiten bol raha hun. "
            "Aapko call kiya tha par shayad aap busy the. "
            "Aapki convenience par hum hamara B2B coffee catalogue aur 12-jar starter sample offer WhatsApp kar sakte hain. "
            "Jab bhi free hon, ek message kar dijiyega. Dhanyawad!"
        ),
        "email_subject": "Quick touchpoint regarding coffee supply for {company} — Purity Beans",
    },
    "CONSENT_CATALOGUE_OPT_IN": {
        "description": "Buyer requested rate list, catalogue or sample information.",
        "whatsapp": (
            "Namaste {name} ji! 🙏\n\n"
            "Jaise humne baat ki thi, yahan Purity Beans ka B2B Wholesale Rate Sheet aur Product Specs hain:\n\n"
            "📦 Special Starter Carton Offer:\n"
            "• 12 Glass Jars (100g Bold Pure Agglomerated Coffee — 100% Pure Coffee, Zero Chicory)\n"
            "• Direct Factory Price: ₹1,999 (Free Express Courier)\n"
            "• Free Tabletop Acrylic Display Stand included\n"
            "• 100% Satisfaction or Money-Back Guarantee\n\n"
            "Aapka dispatch address yahan confirm kar dijiye, hum aaj hi sample carton nikalwa dete hain."
        ),
        "email_subject": "Purity Beans — B2B Catalogue, Margin Breakdown & Starter Carton Offer",
    },
    "CALLBACK_SCHEDULED": {
        "description": "Scheduled callback at a specific time requested by the decision maker.",
        "ai_call": {
            "opening": "Namaste {name} ji, Purity Beans se Hiten bol raha hun. Aapne coffee sampling aur rates ke baare mein baat karne ke liye is waqt ka time diya tha.",
            "hook": "Maine hamari commercial rate list aur 12-jar starter trial carton details ready rakhi hain.",
            "cta": "Kya aap chahenge ki main delivery address verify karke starter carton aaj dispatch schedule kar dun?",
        },
        "whatsapp": (
            "Namaste {name} ji, humne {time} par callback schedule kiya tha. "
            "Agar aap free hain toh main call connect karun, ya WhatsApp par starter carton order finalize karein?"
        ),
    },
    "PRICE_OBJECTION": {
        "description": "Buyer says: 'Too expensive' or 'Local brands are cheaper'.",
        "ai_call": {
            "rebuttal": "Main samajh sakta hun {name} ji. Par market mein 70% brands coffee mein 30-40% chicory mix karte hain. Purity Beans 100% pure premium coffee hai — iska ek cup aapko ₹4.20 ka padta hai, aur counter par ₹18 se ₹25 mein easily bikta hai. Net 76% profit margin hai aapke liye.",
            "cta": "Aap sirf ek 12-jar starter carton test kijiye, agar taste aur sales pasand na aaye toh 100% refund.",
        },
        "whatsapp": (
            "Namaste {name} ji, pricing ke baare mein quick clarity:\n\n"
            "Purity Beans 100% pure coffee hai (Zero Chicory). Yield double aati hai:\n"
            "☕ Cost per cup: Sirf ₹4.20\n"
            "💰 Customer selling price: ₹18 - ₹25\n"
            "📈 Profit per cup: ₹14+ (76% gross profit)\n\n"
            "Test run ke liye hamara 12-jar starter trial pack risk-free hai with money-back guarantee."
        ),
    },
    "SUPPLIER_OBJECTION": {
        "description": "Buyer says: 'Already have a supplier' (e.g. Nescafe/Bru/Local vendor).",
        "ai_call": {
            "rebuttal": "Bilkul theek hai sir, aap apne regular bulk supply vendor ko continue rakhiye. Hum aapka supplier replace nahi karna chahte. Hamara suggestion hai ki front counter par hamara premium 100% pure glass jar display rakhein un customers ke liye jo rich premium coffee mangte hain.",
            "cta": "Aap bas ek counter display carton (12 jars) test kijiye. Isse aapki premium billing increase hogi.",
        },
        "whatsapp": (
            "Ji {name} ji, hum aapke existing supplier ko replace nahi karna chahte. "
            "Aap unse bulk purchase continue rakhein. "
            "Purity Beans premium counter wedge ke roop mein display hota hai — customer higher margin par glass jar buy karta hai. "
            "Ek 12-jar counter carton display laga kar dekhiye, result khud dikhega!"
        ),
    },
    "SAMPLE_FOLLOWUP_CLOSE": {
        "description": "Follow-up after sample kit / tasting was received by the buyer.",
        "ai_call": {
            "opening": "Namaste {name} ji, Purity Beans se Hiten bol raha hun. Aapke pass hamara coffee sample deliver ho gaya tha — taste kaisa laga aapki team ko?",
            "hook": "Hamare sabhi cafe aur retail partners ne aroma aur rich crema ko bahut pasand kiya hai.",
            "cta": "Aapka pehla commercial batch (12-jar starter carton ya 5-carton distributor lot) confirm karein?",
        },
        "whatsapp": (
            "Namaste {name} ji! Umeed hai aapko hamara coffee sample pasand aaya hoga ☕\n\n"
            "Commercial trial ke liye kya hum aapke yahan hamara 12-Jar Starter Carton @ ₹1,999 dispatch karein? "
            "Free shipping aur counter display stand sath mein aayega. "
            "Please confirm kar dijiye!"
        ),
    },
    "OPT_OUT_SUPPRESSION": {
        "description": "Buyer requested not to be contacted.",
        "whatsapp": (
            "Namaste {name} ji, humne aapka preference note kar liya hai aur future communication stop kar diya hai. "
            "Aapko kisi bhi pareshani ke liye kshama chahte hain. Shubh din!"
        ),
        "ai_call": {
            "closing": "Ji bilkul, main turant aapka number hamari calling list se remove kar raha hun. Apologies for disturbing you. Have a great day.",
        },
    },
}


# ── Conversion Maximization Logic ─────────────────────────────────────────────

def get_daily_hot_20(db: Any, limit: int = 20) -> List[HotLeadCandidate]:
    """
    Scans all open leads and identifies the top 20 highest-probability
    leads to focus outreach on today.
    """
    from app.models.models import B2BLead

    terminal_statuses = [
        "DO_NOT_CONTACT", "OPTED_OUT", "WRONG_NUMBER",
        "CLOSED_UNRECOVERABLE", "PAID_CONVERSION", "DISQUALIFIED"
    ]

    candidates = db.query(B2BLead).filter(
        ~B2BLead.status.in_(terminal_statuses),
        B2BLead.do_not_call == False
    ).all()

    scored_leads: List[HotLeadCandidate] = []
    for lead in candidates:
        eligibility = evaluate_lead_eligibility(lead, db=db)
        if not eligibility.any_eligible:
            continue

        intent = evaluate_intent(lead)
        phone = getattr(lead, "phone", "") or getattr(lead, "whatsapp_number", "") or ""
        email = getattr(lead, "email", "") or ""
        city = getattr(lead, "city", "") or ""
        category = getattr(lead, "segment", "") or getattr(lead, "division", "") or "General"
        contact_name = getattr(lead, "contact_name", "") or ""
        company = getattr(lead, "company", "") or getattr(lead, "name", "") or f"Lead #{lead.id}"

        scored_leads.append(
            HotLeadCandidate(
                lead_id=lead.id,
                company_name=company,
                contact_name=contact_name,
                phone=phone,
                email=email,
                city=city,
                category=category,
                intent_score=intent.score,
                recommended_channel=eligibility.recommended_first_touch,
                wedge_offer=intent.wedge_offer,
                action_item=intent.conversion_boost_action,
            )
        )

    # Sort descending by intent score
    scored_leads.sort(key=lambda x: x.intent_score, reverse=True)
    return scored_leads[:limit]


def get_scenario_script(scenario_key: str, channel: str = "ai_call") -> Any:
    """
    Retrieves the battle-tested script or message template for a specific scenario.
    """
    scen = SCENARIO_SCRIPTS.get(scenario_key.upper())
    if not scen:
        scen = SCENARIO_SCRIPTS["COLD_INTRODUCTION"]
    return scen.get(channel, "")
