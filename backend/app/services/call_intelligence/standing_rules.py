"""Standing founder rules that no learning, brief or variant may override.

These can only REFUSE a dial. They are checked in the dial path in addition
to (never instead of) founder_call_pipeline.may_place_ai_call.
"""
from __future__ import annotations

import re

# Founder: never dial this number, under any lead.
NEVER_DIAL_NUMBERS = frozenset({"8591977190"})

# Founder: explicit do-not-call businesses. A rule matches when ALL name
# tokens appear in the normalised company name and, if the rule has a city,
# the lead's city matches too (or the lead has no city on record).
DO_NOT_CALL_BUSINESSES = (
    {"name": ("saltnpeppr",), "city": None, "label": "Salt'n Peppr, Faridkot"},
    {"name": ("salt", "pep"), "city": "faridkot", "label": "Salt'n Peppr, Faridkot"},
    {"name": ("spoton", "kkresidency"), "city": None,
     "label": "SPOT ON Hotel KK Residency, Muktsar"},
    {"name": ("kkresidency",), "city": "muktsar",
     "label": "SPOT ON Hotel KK Residency, Muktsar"},
)


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def number_blocked(phone) -> bool:
    d = _digits(phone)
    return bool(d) and any(d.endswith(n) for n in NEVER_DIAL_NUMBERS)


def business_blocked(company, city=None) -> str | None:
    name = _norm(company)
    c = _norm(city)
    if not name:
        return None
    for rule in DO_NOT_CALL_BUSINESSES:
        if not all(tok in name for tok in rule["name"]):
            continue
        if rule["city"] and c and rule["city"] not in c:
            continue
        return rule["label"]
    return None


def call_blocked(lead) -> str | None:
    """Reason string if a standing rule forbids calling this lead, else None."""
    for field in ("phone", "whatsapp_number", "consent_phone"):
        if number_blocked(getattr(lead, field, None)):
            return f"standing_rule: number {'x' * 6}{_digits(getattr(lead, field, ''))[-4:]} is never dialled"
    label = business_blocked(getattr(lead, "company", None), getattr(lead, "city", None))
    if label:
        return f"standing_rule: {label} is on the founder do-not-call list"
    return None
