"""Festive outreach priority (mid-Oct through mid-Nov 2026).

Category-first, then city density / fit. Used by BOTH channels independently:
  - AI call cycle (category_focus_rank & festive_rank in the merge; PRIORITY_SEGMENT_CASE
    mirrors it for SQL-side ordering)
  - Email sequence due_now ranking

Fast-cash festive encashment order:
  1 Gifting & Hampers (curators, corporate festive gifting, aesthetic glass jars)
    & Distributors / Wholesalers / Stockists / Cash & Carry (25% TD + 5% CD = 30% margin)
  2 Supermarkets / Supermarts / Hypermarkets / Modern Trade
  3 Banquets & Caterers (wedding & festive party season)
  4 Hotels & Hospitality
  5 Corporates / Pantry
  6 Cafes / Restaurants
  7 Kirana / General Retail
  9 Everything else

Hybrid Daily Category Focus:
  Mon: Distributors & Wholesalers
  Tue: Retail Chains & Supermarkets
  Wed: Gifting & Festive Hampers
  Thu: HORECA (Hotels, Banquets, Caterers)
  Fri: Exporters & Institutional Buyers
  Sat: Corporate Pantries & General Retail
  Sun: Closed (no calls)

Override:
  Set CATEGORY_FOCUS=gifting (or distributor, retail_chains, horeca, exports_institutional)
  Set CATEGORY_FOCUS=auto for automatic day-of-week rotation (default)
  Set CATEGORY_FOCUS=off for pure festive tier ranking without single-category boost.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Lower = higher priority.
FESTIVE_CATEGORY_RANK: dict[str, int] = {
    # 1 Gifting & Hampers (Fastest festive cash-in) + Distributors / Wholesalers (30% margin)
    "corporate_gifting": 1, "gifting": 1, "hamper": 1, "festive_gifting": 1,
    "distributor": 1, "wholesaler": 1, "wholesale": 1, "stockist": 1,
    "super_stockist": 1, "wholesaler_agglo": 1, "cash_and_carry": 1,
    # 2 supermarkets / modern trade
    "supermarket": 2, "supermart": 2, "hypermarket": 2, "modern_trade": 2,
    "grocery_chain": 2, "retail_chain": 2,
    # 3 banquets & caterers (wedding & party season)
    "caterer": 3, "catering": 3, "catering_contractor": 3,
    "event_catering": 3, "banquet": 3, "banquet_hall": 3,
    # 4 hotels
    "hotel": 4, "resort": 4, "guest_house": 4, "hotel_canteen": 4,
    # 5 corporates / pantry
    "corporate_office": 5, "corporate": 5, "office_pantry": 5,
    "facility_management": 5, "institutional_buyer": 5, "manufacturing": 5,
    # 6 cafes / restaurants
    "cafe": 6, "restaurant": 6, "horeca": 6, "dhaba": 6,
    # 7 kirana / general retail
    "retail_kirana": 7, "kirana_store": 7, "grocery": 7, "retail": 7,
}

FESTIVE_DEFAULT_RANK = 9

# Daily Category Focus Mapping (Mon=0 to Sat=5, Asia/Kolkata):
DAY_OF_WEEK_CATEGORY: dict[int, str] = {
    0: "distributor",          # Monday
    1: "retail_chains",        # Tuesday
    2: "gifting",              # Wednesday
    3: "horeca",               # Thursday
    4: "exports_institutional",# Friday
    5: "other_corporate",      # Saturday
}

CATEGORY_KEYWORDS: dict[str, set[str]] = {
    "gifting": {
        "corporate_gifting", "gifting", "hamper", "festive_gifting", "gift"
    },
    "distributor": {
        "distributor", "wholesaler", "wholesale", "stockist", "super_stockist",
        "cash_and_carry", "wholesaler_agglo"
    },
    "retail_chains": {
        "supermarket", "supermart", "hypermarket", "modern_trade", "grocery_chain", "retail_chain"
    },
    "horeca": {
        "hotel", "resort", "guest_house", "hotel_canteen", "caterer", "catering",
        "catering_contractor", "event_catering", "banquet", "banquet_hall",
        "cafe", "restaurant", "horeca", "dhaba"
    },
    "exports_institutional": {
        "export", "exporter", "exporters", "institutional_buyer", "facility_management", "manufacturing"
    },
    "other_corporate": {
        "corporate_office", "corporate", "office_pantry", "retail_kirana",
        "kirana_store", "grocery", "retail", "general", "other"
    }
}

GIFTING_NAME_RE = re.compile(
    r"(gifts?|gifting|hampers?|festive\s*gifts?|gift\s*gallery|gift\s*palace|gift\s*house|gift\s*centre)", re.I)
WHOLESALE_NAME_RE = re.compile(
    r"(wholesale|wholesalers?|super\s*stockists?|stockists?|distributors?|"
    r"distribution|cash\s*(?:&|and|n)\s*carry)", re.I)
SUPERMARKET_NAME_RE = re.compile(
    r"(super\s*markets?|hyper\s*markets?|super\s*marts?|mega\s*marts?|marts?)", re.I)


def category_key(lead) -> str:
    for attr in ("searched_category", "division", "segment"):
        v = (getattr(lead, attr, None) or "").strip().lower()
        if v and v != "unknown":
            return v
    return ""


def name_rank(name: str | None) -> int | None:
    n = name or ""
    if GIFTING_NAME_RE.search(n):
        return 1
    if WHOLESALE_NAME_RE.search(n):
        return 1
    if SUPERMARKET_NAME_RE.search(n):
        return 2
    return None


def festive_rank(lead_or_key) -> int:
    if isinstance(lead_or_key, str):
        return FESTIVE_CATEGORY_RANK.get(lead_or_key.strip().lower(), FESTIVE_DEFAULT_RANK)
    key = category_key(lead_or_key)
    rank = FESTIVE_CATEGORY_RANK.get(key, FESTIVE_DEFAULT_RANK)
    if rank >= 5 or key == "":
        nr = name_rank(getattr(lead_or_key, "company", None))
        if nr is not None:
            return nr
    return rank


def active_focus_category(now=None) -> str:
    """Return the active focus category:
    1. Reads environment variable CATEGORY_FOCUS (e.g. 'gifting', 'distributor', 'horeca', 'retail_chains', 'exports_institutional').
    2. If 'auto' (default), maps to current IST day of week (Monday=distributor, Tuesday=retail_chains, Wednesday=gifting, Thursday=horeca, Friday=exports_institutional, Saturday=other_corporate).
    3. If 'none' or 'off', returns empty string (no daily single-category boost; standard festive order applies).
    """
    cfg = (os.getenv("CATEGORY_FOCUS", "auto") or "auto").strip().lower()
    if cfg in ("none", "off", "0", "all"):
        return ""
    if cfg in CATEGORY_KEYWORDS:
        return cfg
    if cfg == "exports":
        return "exports_institutional"
    if cfg in ("supermarket", "supermarkets", "retail"):
        return "retail_chains"
    if cfg in ("wholesaler", "wholesalers", "distributors"):
        return "distributor"
    if cfg == "auto":
        ist_now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Kolkata"))
        weekday = ist_now.weekday()
        return DAY_OF_WEEK_CATEGORY.get(weekday, "distributor")
    return ""


def is_category_match(lead, category_name: str) -> bool:
    """Check if lead belongs to the target category."""
    if not category_name:
        return False
    kw_set = CATEGORY_KEYWORDS.get(category_name, set())
    key = category_key(lead)
    if key in kw_set:
        return True
    cname = getattr(lead, "company", "") or ""
    if category_name == "gifting" and GIFTING_NAME_RE.search(cname):
        return True
    if category_name == "distributor" and WHOLESALE_NAME_RE.search(cname):
        return True
    if category_name == "retail_chains" and SUPERMARKET_NAME_RE.search(cname):
        return True
    if category_name == "exports_institutional" and re.search(r"(exports?|exporters?|import)", cname, re.I):
        return True
    return False


def category_focus_rank(lead) -> int:
    """0 = matches today's focus category (highest priority for today's conversions);
    1 = does not match (dialed after today's focus leads)."""
    target = active_focus_category()
    if target and is_category_match(lead, target):
        return 0
    return 1


_COFFEE_FIT_RE = re.compile(r"\[INSTANT_COFFEE_FIT [^\]]*\]\s*(CONFIRMED|LIKELY|UNLIKELY)")
COFFEE_FIT_ORDER: dict[str, int] = {"CONFIRMED": 0, "LIKELY": 1, "UNLIKELY": 2}


def coffee_fit_rank(lead) -> int:
    """0 = CONFIRMED, 1 = LIKELY / not assessed, 2 = UNLIKELY."""
    for attr in ("coffee_buying_evidence", "qualification_notes"):
        hits = _COFFEE_FIT_RE.findall(getattr(lead, attr, None) or "")
        if hits:
            return COFFEE_FIT_ORDER.get(hits[-1], 1)
    return 1


def coffee_fit_tier(lead) -> str:
    """14 Oct fit gate: CONFIRMED / LIKELY (assessed) / UNASSESSED / UNLIKELY."""
    for attr in ("coffee_buying_evidence", "qualification_notes"):
        hits = _COFFEE_FIT_RE.findall(getattr(lead, attr, None) or "")
        if hits:
            return hits[-1]
    return "UNASSESSED"


def fit_gate_rank(lead) -> int:
    """Primary AI-call queue key when AI_CALL_FIT_GATE=1 (default):
    0 = CONFIRMED/LIKELY, 1 = not assessed, 2 = UNLIKELY. Nothing is
    removed - lower tiers are dialled only after the higher ones."""
    if os.getenv("AI_CALL_FIT_GATE", "1").strip() == "0":
        return 0
    t = coffee_fit_tier(lead)
    return 0 if t in ("CONFIRMED", "LIKELY") else (2 if t == "UNLIKELY" else 1)


def festive_label(rank: int) -> str:
    return {
        1: "gifting_distributors_wholesalers",
        2: "supermarkets_modern_trade",
        3: "banquets_caterers",
        4: "hotels",
        5: "corporates_pantry",
        6: "cafes_restaurants",
        7: "kirana",
    }.get(rank, "other")
