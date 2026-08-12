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

SIGNATURE = (
    "\n\nWarm Regards,\nHiten Jain\nFounder | Pure Pantry Provisions\n"
    "📞 +91 90849 58495 | WhatsApp: +91 98555 93323\n"
    "p3online.in | connect@purepantryprovisions.com"
)


def _greet(name: str) -> str:
    return f"Dear {name}," if name else "Dear Sir/Madam,"


def _wa_greet(name: str) -> str:
    return f"Namaste {name} ji" if name else "Namaste"


# ── Registry ─────────────────────────────────────────────────────────────────
# Key = canonical segment. Each entry:
#   email_subject: str template ({company}, {city})
#   whatsapp:      callable(name, city) -> str
#   ai_script:     list of {label, text-template} steps ({name}, {city})

TEMPLATES: dict[str, dict] = {
    "distributor": {
        "email_subject": "Distribution Partnership — Purity Beans Premium Coffee",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nMain Hiten Jain hun — Purity Beans ka Founder.\n"
            f"{c or 'aapke sheher'} mein distribution ke liye email bheja tha.\n\n"
            "• Margin: 35-42%\n• Free sample kit\n\nKya 15 min milenge?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening",      "text": '"Namaste, kya main {name} ji se baat kar sakta hun?"'},
            {"label": "Introduction", "text": '"Ji, main Purity Beans coffee ki taraf se call kar raha hun — email aur WhatsApp bheja tha."'},
            {"label": "Hook",         "text": '"Hum {city} mein distributor partner dhundh rahe hain. 5 minutes hain?"'},
            {"label": "Value Prop",   "text": '"Distributor margin 35-42% aur free sample kit — koi commitment nahi."'},
            {"label": "CTA",          "text": '"Kya is hafte ek choti meeting rakh sakte hain?"'},
        ],
    },
    "retail": {
        "email_subject": "Stock Purity Beans Coffee — Retailer Partnership",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans premium coffee — 22-28% margin, shelf-ready.\n\n"
            "Free sample pack bhej sakta hun?\n+91 90849 58495"
        ),
        "ai_script": [
            {"label": "Opening", "text": '"Namaste, {name} ji se baat ho sakti hai?"'},
            {"label": "Pitch",   "text": '"Purity Beans coffee {city} mein stock karne ke baare mein call kar raha tha."'},
            {"label": "Hook",    "text": '"22-28% margin aur shelf-ready packaging — kya aap sample dekhna chahenge?"'},
            {"label": "CTA",     "text": '"Kya is hafte sample pack bhej sakta hun?"'},
        ],
    },
    "horeca": {
        "email_subject": "Premium Coffee Supply | Purity Beans",
        "whatsapp": lambda n, c: (
            f"{_wa_greet(n)}\n\nPurity Beans coffee supply. Free tasting session arrange kar sakte hain.\n\n"
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
        "whatsapp": lambda n, c: "",   # No WhatsApp for government procurement
        "ai_script": [],               # No AI cold-calls to government offices
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
}

# Aliases: division/segment values seen in the DB → canonical template key
SEGMENT_ALIASES = {
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


def resolve_segment(value: str | None) -> str:
    """Map any division/segment string from the DB to a canonical template key."""
    v = (value or "corporate").strip().lower()
    if v in TEMPLATES:
        return v
    return SEGMENT_ALIASES.get(v, "corporate")


# ── Multi-channel warming ────────────────────────────────────────────────────
# Full channel set a lead can be warmed through. Order = warming sequence.
CHANNELS = ["email", "whatsapp", "ai_call", "linkedin", "facebook"]

CHANNEL_LABELS = {
    "email": "Email", "whatsapp": "WhatsApp", "ai_call": "AI Call (Hindi)",
    "linkedin": "LinkedIn", "facebook": "Facebook",
}

# Segment value-prop line reused across LinkedIn / Facebook copy — real claims only.
_SEGMENT_VALUEPROP = {
    "distributor":   "distributor margins of 35-42% on 100% pure instant coffee (zero chicory), with a free sample kit",
    "retail":        "shelf-ready 100% pure instant coffee at 22-28% retail margin, with a free sample pack",
    "wholesale":     "bulk 100% pure instant coffee at 35-42% margin",
    "horeca":        "premium 100% pure instant coffee for your establishment, with a free tasting",
    "corporate":     "an office coffee pantry program 30-40% cheaper than retail",
    "gifting":       "premium coffee gift hampers from ₹499 with custom branding",
    "private_label": "private-label coffee manufacturing, MOQ 100 kg, FSSAI compliant",
    "government":    "FSSAI-licensed, GST-registered institutional coffee supply",
}


def _valueprop(segment: str) -> str:
    return _SEGMENT_VALUEPROP.get(resolve_segment(segment), _SEGMENT_VALUEPROP["corporate"])


def linkedin_message(segment: str, name: str = "", city: str = "") -> str:
    """Professional LinkedIn connection note (English) — segment-tailored, factual."""
    greeting = f"Hi {name}," if name else "Hello,"
    loc = f" in {city}" if city else ""
    return (
        f"{greeting} I'm Hiten Jain, founder of Purity Beans (Pure Pantry Provisions). "
        f"We offer {_valueprop(segment)}. We're building partnerships{loc} and I'd value "
        f"connecting to share our catalogue. Open to a quick chat?"
    )


def facebook_message(segment: str, name: str = "", city: str = "") -> str:
    """Facebook / page DM outreach (English) — segment-tailored, factual."""
    greeting = f"Hi {name}," if name else "Hi there,"
    return (
        f"{greeting} greetings from Purity Beans — 100% pure instant coffee, no chicory, "
        f"FSSAI licensed. We offer {_valueprop(segment)}. Could I send you our catalogue and "
        f"a complimentary sample? — Hiten, +91 90849 58495"
    )


def get_templates(segment: str, name: str = "", city: str = "") -> dict:
    """Return JSON-safe rendered templates for a segment across ALL channels."""
    key = resolve_segment(segment)
    t = TEMPLATES[key]
    return {
        "segment": key,
        "email_subject": t["email_subject"],
        "whatsapp_message": t["whatsapp"](name, city),
        "ai_script": [
            {"label": s["label"], "text": s["text"].replace("{name}", name or "aap").replace("{city}", city or "aapke sheher")}
            for s in t["ai_script"]
        ],
        "linkedin_message": linkedin_message(key, name, city),
        "facebook_message": facebook_message(key, name, city),
    }


def channel_default_text(segment: str, channel: str) -> str:
    """A representative script for a segment×channel — used by the approval UI."""
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
    return ""


def whatsapp_for_lead(lead) -> str:
    """Render the segment-appropriate WhatsApp intro message for a lead."""
    key = resolve_segment(lead.division or lead.segment)
    name = (lead.contact_name or "").split()[0] if lead.contact_name else ""
    return TEMPLATES[key]["whatsapp"](name, lead.city or "")
