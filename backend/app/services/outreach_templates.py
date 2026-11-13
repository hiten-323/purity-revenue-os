"""
Outreach Template Registry — single source of truth for all channel templates.

V1.1 rule: the UI never hardcodes email subjects, bodies, WhatsApp messages
or AI call scripts. Everything is served from here via GET /templates/{segment}.

Formal first-touch EMAILS still come from email_sender.generate_b2b_pitch_email
(which runs the quality gate rules); this registry provides the WhatsApp
messages, AI Hindi call scripts, and per-segment subject lines that were
previously duplicated in FounderActionEngine.tsx and FounderGrowthHub.tsx.
"""

from __future__ import annotations

import os

SIGNATURE = (
    "\n\nWarm Regards,\nHiten Jain\nFounder | Pure Pantry Provisions\n"
    "📞 +91 90849 58495 | WhatsApp: +91 98555 93323\n"
    "Website: https://p3online.in | connect@purepantryprovisions.com"
)


def _greet(name: str) -> str:
    return f"Dear {name}," if name else "Dear Sir/Madam,"


def _wa_greet(name: str) -> str:
    return f"Namaste {name} ji" if name else "Namaste"


def _instagram_post() -> str:
    return (
        (os.getenv("SOCIAL_INSTAGRAM_POST") or "").strip()
        or (os.getenv("DOC_INSTAGRAM") or "").strip()
        or "https://www.instagram.com/p/Db_nFMaAieK/"
    )


# ── Registry ─────────────────────────────────────────────────────────────────
TEMPLATES: dict[str, dict] = {
    "distributor": {
        "email_subject": "Distribution Partnership — Purity Beans Premium Coffee",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nMain Hiten Jain hun — Purity Beans ka Founder.\n"
            f"{c or 'aapke sheher'} mein distribution ke liye email bheja tha.\n\n"
            "• Margin: 35-42%\n• Direct factory supply\n\nKya 15 min milenge?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening",      "text": '"Namaste, kya main {name} ji se baat kar sakta hun?"'},
            {"label": "Introduction", "text": '"Ji, main Purity Beans coffee ki taraf se call kar raha hun — email aur WhatsApp bheja tha."'},
            {"label": "Hook",         "text": '"Hum {city} mein distributor partner dhundh rahe hain. 5 minutes hain?"'},
            {"label": "Value Prop",   "text": '"Distributor margin 35-42% aur direct factory terms — koi commitment nahi."'},
            {"label": "CTA",          "text": '"Kya is hafte ek choti meeting rakh sakte hain?"'},
        ],
    },
    "retail": {
        "email_subject": "Stock Purity Beans Coffee — Retailer Partnership",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans premium coffee — 22-28% margin, shelf-ready.\n\n"
            "Catalogue aur trade terms bhej sakta hun?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} ji se baat ho sakti hai?"'},
            {"label": "Pitch",   "text": '"Purity Beans coffee {city} mein stock karne ke baare mein call kar raha tha."'},
            {"label": "Hook",    "text": '"22-28% margin aur shelf-ready packaging — kya aap catalogue dekhna chahenge?"'},
            {"label": "CTA",     "text": '"Kya is hafte catalogue share kar sakta hun?"'},
        ],
    },
    "horeca": {
        "email_subject": "Premium Coffee Supply | Purity Beans",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans coffee supply. Factory wholesale rates available hain.\n\n"
            "10 min milenge?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening",  "text": '"Namaste, {name} ji se baat ho sakti hai?"'},
            {"label": "Pitch",    "text": '"Purity Beans premium coffee supply ke baare mein call kar raha tha."'},
            {"label": "Qualify",  "text": '"Aap currently coffee kahan se lete hain aur monthly kitna use hota hai?"'},
            {"label": "Value",    "text": '"Hum premium instant coffee wholesale price par dete hain — free tasting ke saath."'},
            {"label": "CTA",      "text": '"Kya is hafte ek tasting session rakh sakte hain?"'},
        ],
    },
    "corporate": {
        "email_subject": "Upgrade Your Office Coffee | Purity Beans Pantry Program",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans office coffee pantry — retail se 30-40% sasta, "
            f"{c or 'aapke office'} ke liye.\n\nPilot ke baare mein baat karein?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} Admin/Procurement team se hain?"'},
            {"label": "Pitch",   "text": '"Purity Beans — office coffee pantry program ke baare mein call kar raha tha."'},
            {"label": "Qualify", "text": '"Aapke office mein kitne employees hain aur coffee kahan se aati hai?"'},
            {"label": "Value",   "text": '"Hum monthly subscription dete hain — retail se 30-40% sasta, auto-replenishment ke saath."'},
            {"label": "CTA",     "text": '"Kya ek small pilot start kar sakte hain?"'},
        ],
    },
    "gifting": {
        "email_subject": "Premium Coffee Gift Hampers | Purity Beans",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nCorporate gifting ke liye premium coffee hampers. "
            "Rs.499 se shuru, custom branding.\n\nCatalogue share karun?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} HR/Admin department se hain?"'},
            {"label": "Pitch",   "text": '"Purity Beans se hun — corporate gifting ke liye premium coffee hampers offer kar raha tha."'},
            {"label": "Hook",    "text": '"Rs.499 se shuru, custom branding aur bulk discount available hai."'},
            {"label": "CTA",     "text": '"Kya catalogue share kar sakta hun?"'},
        ],
    },
    "private_label": {
        "email_subject": "Private Label Coffee | Pure Pantry Provisions",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPrivate label coffee — custom packaging, MOQ 100 kg, "
            "pilot 2 weeks mein.\n\nBaat karein?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening",    "text": '"Namaste, {name} ji se baat ho sakti hai?"'},
            {"label": "Pitch",      "text": '"Private label coffee ke baare mein email kiya tha."'},
            {"label": "Qualify",    "text": '"Aapka brand kis segment mein hai aur monthly volume kya hoga?"'},
            {"label": "Value Prop", "text": '"MOQ 100 kg se, custom packaging, FSSAI compliant — pilot 2 weeks mein."'},
            {"label": "CTA",        "text": '"Kya ek NDA sign karke pilot discuss kar sakte hain?"'},
        ],
    },
    "government": {
        "email_subject": "Coffee Supply Bid | Purity Beans — Pure Pantry Provisions",
        "whatsapp": lambda n, c: "",
        "ai_script": [],
    },
    "wholesale": {
        "email_subject": "Wholesale Coffee Supply — Purity Beans",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans wholesale coffee — bulk pack, 35-42% margin.\n\n"
            "Rate list bhej sakta hun?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} ji se baat ho sakti hai?"'},
            {"label": "Pitch",   "text": '"Purity Beans wholesale supply ke baare mein call kar raha tha."'},
            {"label": "Hook",    "text": '"Bulk pack 35-42% margin ke saath — rate list bhejun?"'},
            {"label": "CTA",     "text": '"Kya is hafte baat kar sakte hain?"'},
        ],
    },
    "coffee_ingredient_manufacturers": {
        "email_subject": "Pure Coffee Ingredients (Zero Chicory) for Commercial Production | Purity Beans",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans commercial coffee ingredients — pure coffee, Zero Chicory, "
            "ultra-fast solubility for baking, beverages, confectionery and cosmetics.\n\n"
            "Catalogue: https://cdn.shopify.com/s/files/1/0712/6315/8459/files/Purity_Beans_Catalogue_2026-10.pdf\n"
            "Bulk specs aur trade terms share karein?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, kya meri baat {name} ji se ho sakti hai — product formulation / procurement team se?"'},
            {"label": "Pitch",   "text": '"Main Purity Beans se Hiten bol raha hun. Hum commercial food, beverage aur bakery brands ko pure soluble coffee supply karte hain."'},
            {"label": "Hook",    "text": '"Market mein chicory adulteration se taste aur fermentation kharab hoti hai. Hamara pure agglomerated coffee zero chicory ke saath perfect aroma deta hai."'},
            {"label": "Value",   "text": '"Instant 30s cold/hot liquid dissolution aur uniform batch consistency — direct factory B2B rates par."'},
            {"label": "CTA",     "text": '"Kya aapki R&D ya production team ke liye commercial catalogue aur specs share karein?"'},
        ],
    },
    "retail_chains": {
        "email_subject": "Stock Purity Beans Pure Coffee Jars — Retail Chain & Supermarket Partnership",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans pure coffee glass jars (Zero Chicory) — 25-30% retail margin, "
            "tabletop display stand included.\n\n"
            "Catalogue: https://cdn.shopify.com/s/files/1/0712/6315/8459/files/Purity_Beans_Catalogue_2026-10.pdf\n"
            "Starter carton (12 jars @ ₹1,999) aur trade terms discuss karein?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} ji se Category Management / Purchase team se baat ho sakti hai?"'},
            {"label": "Pitch",   "text": '"Purity Beans premium coffee glass jars aapke retail stores mein stock karne ke silsile mein call kiya hai."'},
            {"label": "Hook",    "text": '"Lead-free glass packaging, 25-30% direct retail margins aur pure coffee claim jo commercial chicory brands se alag khada karta hai."'},
            {"label": "CTA",     "text": '"Kya is hafte ek 12-jar starter display pack test ke liye discuss karein?"'},
        ],
    },
    "hospitality_institutional": {
        "email_subject": "Premium Commercial Coffee Supply | Purity Beans HoReCa",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans pure coffee supply. Sirf ₹4.20 per cup serving cost aur 78% margin counter par.\n\n"
            "Catalogue: https://cdn.shopify.com/s/files/1/0712/6315/8459/files/Purity_Beans_Catalogue_2026-10.pdf\n"
            "Wholesale trade terms discuss karein?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening",  "text": '"Namaste, kya meri baat {name} ji F&B Manager ya Purchase Head se ho sakti hai?"'},
            {"label": "Pitch",    "text": '"Purity Beans pure commercial coffee supply ke baare mein call kiya hai."'},
            {"label": "Hook",     "text": '"Hamara 100g jar 50 cups banata hai — ₹4.20 cost per cup. Counter par ₹20-50 mein serve karke 78% gross margin milta hai."'},
            {"label": "CTA",      "text": '"Kya aapke chefs ya beverage team ke liye free tasting kit schedule karein?"'},
        ],
    },
}

SEGMENT_ALIASES = {
    "distributor_wholesaler": "distributor",
    "corporates": "corporate",
    "gifting_companies": "gifting",
    "bakery": "coffee_ingredient_manufacturers",
    "brewery": "coffee_ingredient_manufacturers",
    "confectionery": "coffee_ingredient_manufacturers",
    "ingredient": "coffee_ingredient_manufacturers",
    "horeca": "hospitality_institutional",
    "grocery": "retail",
    "modern_trade": "retail",
    "retail_chain": "retail",
    "kirana": "retail",
    "wholesaler_agglo": "wholesale",
    "corporate_pantry": "corporate",
    "corporate_gifting": "gifting",
    "tender": "government",
    "gem": "government",
    "psu": "government",
    "hotel": "horeca",
    "restaurant": "horeca",
    "cafe": "horeca",
    "school": "corporate",
    "college": "corporate",
    "university": "corporate",
    "hospital": "corporate",
    "healthcare": "corporate",
    "export": "private_label",
}


def resolve_segment(value: str | None, company: str | None = None, description: str | None = None) -> str:
    if value and str(value).strip().lower() in TEMPLATES:
        return str(value).strip().lower()
    try:
        from app.services.category_classifier import classify_macro_outreach_segment
        macro_key, _ = classify_macro_outreach_segment(division=value, company=company, description=description)
        if macro_key in TEMPLATES:
            return macro_key
    except Exception:
        pass
    v = (value or "corporate").strip().lower()
    return SEGMENT_ALIASES.get(v, "retail_chains")


CHANNELS = ["email", "whatsapp", "ai_call", "linkedin", "facebook", "instagram"]

CHANNEL_LABELS = {
    "email": "Email",
    "whatsapp": "WhatsApp",
    "ai_call": "AI Call (Hindi)",
    "linkedin": "LinkedIn",
    "facebook": "Facebook",
    "instagram": "Instagram",
}

_SEGMENT_VALUEPROP = {
    "distributor":   "distributor margins of 35-42% on pure instant coffee (zero chicory), with Starter Cartons @ Rs 1,999",
    "retail":        "shelf-ready pure instant coffee at 25-30% retail margin with display stands",
    "wholesale":     "bulk pure instant coffee at 35-42% margin with official trade invoicing",
    "horeca":        "pure instant coffee at Rs 4.20/cup with 78% cup margin",
    "corporate":     "an office coffee pantry program 30% vs retail",
    "gifting":       "custom corporate coffee gift hampers from Rs 499",
    "private_label": "private-label coffee manufacturing, MOQ 100 kg, FSSAI compliant",
    "manufacturer":  "consistent industrial pure coffee supply for bakeries and confectionery",
    "government":    "FSSAI-licensed institutional pure coffee supply with official B2B trade invoicing",
}


def _valueprop(segment: str) -> str:
    return _SEGMENT_VALUEPROP.get(resolve_segment(segment), _SEGMENT_VALUEPROP["corporate"])


def linkedin_message(segment: str, name: str = "", city: str = "") -> str:
    greeting = f"Hi {name}," if name else "Hello,"
    loc = f" in {city}" if city else ""
    return (
        f"{greeting} I'm Hiten Jain, founder of Purity Beans (Pure Pantry Provisions). "
        f"We offer {_valueprop(segment)}. We're building partnerships{loc} and I'd value "
        f"connecting to share our catalogue. Open to a quick chat?"
    )


def facebook_message(segment: str, name: str = "", city: str = "") -> str:
    greeting = f"Hi {name}," if name else "Hi there,"
    return (
        f"{greeting} greetings from Purity Beans — 100% pure instant coffee, no chicory, "
        f"FSSAI licensed. We offer {_valueprop(segment)}. Could I send you our catalogue and "
        f"a complimentary sample? — Hiten, +91 90849 58495"
    )


def instagram_message(segment: str, name: str = "", city: str = "") -> str:
    """
    Instagram DM / comment follow-up copy. Links the configured social proof post.
    Cold Instagram automation is not enabled — founder approves before any send.
    """
    greeting = f"Hi {name}," if name else "Hi,"
    post = _instagram_post()
    return (
        f"{greeting} Hiten here from Purity Beans (Pure Pantry Provisions). "
        f"We offer {_valueprop(segment)}. "
        f"Here's a recent post if useful: {post} "
        f"Happy to share catalogue + sample — +91 90849 58495"
    )


def get_templates(segment: str, name: str = "", city: str = "") -> dict:
    key = resolve_segment(segment)
    t = TEMPLATES[key]
    return {
        "segment": key,
        "email_subject": t["email_subject"],
        "whatsapp_message": t["whatsapp"](name, city),
        "ai_script": [
            {
                "label": s["label"],
                "text": s["text"]
                .replace("{name}", name or "aap")
                .replace("{city}", city or "aapke sheher"),
            }
            for s in t["ai_script"]
        ],
        "linkedin_message": linkedin_message(key, name, city),
        "facebook_message": facebook_message(key, name, city),
        "instagram_message": instagram_message(key, name, city),
        "instagram_post": _instagram_post(),
    }


def channel_default_text(segment: str, channel: str) -> str:
    key = resolve_segment(segment)
    t = TEMPLATES[key]
    if channel == "email":
        return (
            f"Subject: {t['email_subject']}\n\n"
            f"Every email is personalised per lead (company, city, segment, buying signals) "
            f"by the AI generator and must pass the quality gate before it reaches you. "
            f"There is no single fixed body for email — review the exact email that will be "
            f"sent for each lead in the Approval Inbox below (click the eye icon on any row).\n\n"
            f"— Hiten Jain, Pure Pantry Provisions"
        )
    if channel == "whatsapp":
        return t["whatsapp"]("", "")
    if channel == "ai_call":
        return "\n".join(f"{s['label']}: {s['text']}" for s in t["ai_script"])
    if channel == "linkedin":
        return linkedin_message(key, "", "")
    if channel == "facebook":
        return facebook_message(key, "", "")
    if channel == "instagram":
        return instagram_message(key, "", "")
    return ""


def whatsapp_for_lead(lead) -> str:
    key = resolve_segment(
        getattr(lead, "division", None) or getattr(lead, "segment", None),
        company=getattr(lead, "company", None),
        description=getattr(lead, "description", None)
    )
    name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
    return TEMPLATES[key]["whatsapp"](name, lead.city or "")
