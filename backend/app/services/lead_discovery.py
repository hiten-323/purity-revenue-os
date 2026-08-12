"""
Purity Beans Lead Discovery Engine
Searches for prospects across Google Maps, IndiaMART, TradeIndia, and LinkedIn.
Returns normalized lead records ready to insert into B2BLead table.
"""

from __future__ import annotations
import os
import re
import json
import time
import httpx
from datetime import datetime
from typing import Optional


# ── Location & Distance Resolver ─────────────────────────────────────────────
import math
import logging

_log = logging.getLogger(__name__)

CITY_COORDINATES = {
    "bathinda": (30.2109, 74.9525),
    "rampura phul": (30.2706, 75.2361),
    "mansa": (29.9882, 75.3900),
    "abohar": (30.1479, 74.1958),
    "fazilka": (30.4037, 74.0305),
    "sri ganganagar": (29.9090, 73.8787),
    "ferozepur": (30.9238, 74.6136),
    "ludhiana": (30.9010, 75.8573),
    "amritsar": (31.6340, 74.8723),
    "jalandhar": (31.3260, 75.5762),
    "chandigarh": (30.7333, 76.7794),
    "patiala": (30.3398, 76.3869),
    "delhi": (28.6139, 77.2090),
    "jaipur": (26.9124, 75.7873),
    "bangalore": (12.9716, 77.5946),
    "mumbai": (18.9750, 72.8258),
    "hyderabad": (17.3850, 78.4867),
    "mohali": (30.7046, 76.7179),
    "panchkula": (30.6942, 76.8606),
    "zirakpur": (30.6425, 76.8178),
    "kharar": (30.7431, 76.6432),
}

def calculate_distance(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float | None:
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    R = 6371.0  # Earth's radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(R * c, 1)

def resolve_cities_in_radius(origin: str, radius_km: float = 100.0) -> list[tuple[str, float]]:
    origin_clean = (origin or "").lower().strip()
    if origin_clean not in CITY_COORDINATES:
        return [(origin, 0.0)]
    
    lat1, lon1 = CITY_COORDINATES[origin_clean]
    nearby = []
    for city, (lat2, lon2) in CITY_COORDINATES.items():
        d = calculate_distance(lat1, lon1, lat2, lon2)
        if d is not None and d <= radius_km:
            nearby.append((city.title(), d))
    nearby.sort(key=lambda x: x[1])
    return nearby

# ── Segment search profiles ───────────────────────────────────────────────────

# DEMAND-SIDE (revenue-not-leads): search for organisations that REQUIRE /
# CONSUME instant coffee in bulk — not businesses that sell it.
# Category-specific queries aligned with the 9 business sweeps specified.
SEARCH_PROFILES = {
    "distributor": [
        "FMCG distributors", "food distributors", "beverage distributors", 
        "grocery distributors", "wholesalers", "general merchandise wholesalers"
    ],
    "retail_kirana": [
        "supermarkets", "grocery stores", "kirana stores", 
        "departmental stores", "modern trade", "grocery chains"
    ],
    "corporate_office": [
        "corporate offices", "large offices", "office pantry providers", 
        "coworking offices", "business parks", "administrative offices"
    ],
    "facility_management": [
        "facility management companies", "corporate catering companies", 
        "pantry management companies", "workplace catering"
    ],
    "hotel": [
        "hotels", "restaurants", "cafes", "caterers", "banquet facilities"
    ],
    "hospital": [
        "hospitals", "schools", "colleges", "universities", "hostels", "institutional canteens"
    ],
    "manufacturing": [
        "factories", "manufacturing plants", "industrial offices", "large employee facilities"
    ],
    "corporate_gifting": [
        "corporate gifting", "private label buyers", "exporters", "institutional suppliers"
    ],
    "govt_canteen": [
        "government procurement", "canteen tenders", "coffee tenders", 
        "beverage tenders", "pantry tenders", "institutional supply tenders"
    ],
}

SEGMENT_ALIASES = {
    "horeca":     ["hotel"],
    "grocery":    ["retail_kirana"],
    "retail":     ["retail_kirana"],
    "gifting":    ["corporate_gifting"],
    "corporate":  ["corporate_office"],
    "wholesale":  ["distributor"],
    "government": ["govt_canteen"],
    "education":  ["hospital"],
    "healthcare": ["hospital"],
}


# ── Real business classification + coffee buying score ───────────────────────
# Google Maps returns a `types` array describing what a place ACTUALLY is.
# Discovery fetched it and threw it away, filing every result under the segment
# we happened to be searching — so "Crazy Coffee", a cafe found while searching
# "distributor", was stored as a distributor and scored on distributor margins.
# These map the real type, and score how likely the business is to buy coffee
# based only on evidence we actually hold.

# maps type -> (our division, coffee buying score, why)
_MAPS_TYPE_MAP = [
    ("cafe",            "cafe",                  95, "Cafe — serves coffee as core product"),
    ("bakery",          "cafe",                  85, "Bakery — serves coffee with food"),
    ("lodging",         "hotel",                 90, "Hotel — breakfast and guest beverage service"),
    ("restaurant",      "restaurant",            80, "Restaurant — beverage service"),
    ("meal_takeaway",   "restaurant",            65, "Food service — beverage attach"),
    ("meal_delivery",   "restaurant",            60, "Food service — beverage attach"),
    ("bar",             "restaurant",            55, "Bar — beverage service"),
    ("supermarket",     "supermarket",           85, "Supermarket — stocks instant coffee"),
    ("grocery_or_supermarket", "retail_kirana",  80, "Grocery retail — stocks instant coffee"),
    ("convenience_store", "retail_kirana",       70, "Convenience retail — stocks instant coffee"),
    ("department_store", "modern_trade",         60, "Department store — FMCG shelf space"),
    ("hospital",        "hospital",              70, "Hospital — canteen and staff consumption"),
    ("school",          "school",                65, "School — mess and staff canteen"),
    ("university",      "college",               70, "College/university — mess and canteens"),
    ("lawyer",          "corporate_office",      45, "Professional office — pantry consumption"),
    ("accounting",      "corporate_office",      45, "Professional office — pantry consumption"),
    ("insurance_agency", "corporate_office",     45, "Professional office — pantry consumption"),
    ("real_estate_agency", "corporate_office",   40, "Office — pantry consumption"),
    ("storage",         "wholesaler",            60, "Warehouse/distribution — bulk trade"),
    ("moving_company",  "facility_management",   40, "Logistics/facilities — possible distribution"),
]

# Name keywords are weaker evidence than Maps types but still real signal.
_NAME_HINTS = [
    (("cafe", "coffee", "espresso", "barista", "brew"), "cafe", 90,
     "Business name indicates coffee service"),
    (("hotel", "resort", "inn", "residency"), "hotel", 85,
     "Hotel — breakfast and guest beverage service"),
    (("restaurant", "dhaba", "kitchen", "food", "caterer", "catering"), "restaurant", 70,
     "Food service business"),
    (("distributor", "distribution", "agencies", "agency", "traders", "trading",
      "enterprises", "sales corporation", "sales corp", "& co", "and co"),
     "distributor", 75, "Distribution/trading business — resells FMCG"),
    (("wholesale", "cash and carry"), "wholesaler", 75, "Wholesale trade — bulk buyer"),
    (("supermarket", "super store", "mart", "bazaar", "store"), "supermarket", 70,
     "Supermarket grocery retail"),
    (("kirana", "provision", "grocery", "hatti", "hattee", "karyana", "kiryana", "parchun"), "retail_kirana", 65,
     "Local retail shop (kirana/general store)"),
    (("hospital", "nursing home", "clinic"), "hospital", 65,
     "Healthcare provider"),
    (("school", "institute"), "school", 60,
     "School education institution"),
    (("college", "university"), "college", 60,
     "College/university education institution"),
    (("gift", "hamper"), "corporate_gifting", 70, "Gifting business — coffee hampers"),
    (("office", "corporate"), "corporate_office", 45, "Office pantry consumption"),
    (("pantry", "vending"), "office_pantry", 45, "Office pantry / vending supply"),
    (("factory", "manufacturing", "industries", "industry"), "manufacturing", 50, "Manufacturing business"),
    (("facility", "maintenance", "cleaning"), "facility_management", 45, "Facility management service"),
    (("exporter", "export"), "exporter", 60, "Exporter business"),
    (("private label", "white label"), "private_label", 60, "Private label buyer"),
    (("procurement", "institutional"), "institutional_buyer", 60, "Institutional buyer"),
]


def _name_hit(keyword: str, name: str) -> bool:
    """
    Does a name-hint keyword genuinely appear in this business name?

    Plain substring matching read "CBS KITCHENWARE" as a food-service business,
    because "kitchen" is inside "kitchenware" — and that scored it 70 and put a
    utensils shop into the pipeline as a Rs 1.8L horeca opportunity.

    So the keyword must END on a word boundary (an optional plural 's' allowed):
      kitchen   matches "Kitchen King"   not "Kitchenware"
      food      matches "Gupta Foods"    (plural kept)
    The left side is deliberately NOT anchored, because Indian retail names run
    the word together — "Bigmart" and "D-Mart" are both grocery, and requiring a
    boundary before "mart" would drop them.
    """
    return re.search(rf"{re.escape(keyword)}s?\b", name) is not None


def classify_business(maps_types: list | None, company: str, fallback_segment: str = "",
                      description: str = "", website_content: str = "") -> dict:
    """
    What this business actually is, and how likely it is to buy coffee.

    Returns division, coffee_buying_score (0-100) and the evidence behind it.
    Order of precedence:
    1. Google Maps types (official category evidence)
    2. Website evidence & structured business descriptions
    3. Name keywords (only as weak fallback)
    4. Default to 'unknown' if no evidence exists (Search intent never becomes category truth).
    """
    types = [str(t).lower() for t in (maps_types or [])]
    name = (company or "").lower()
    desc_clean = ((description or "") + " " + (website_content or "")).lower()

    # 1. Google Maps Types (Official Category Evidence)
    for key, division, score, why in _MAPS_TYPE_MAP:
        if key in types:
            return {"division": division, "coffee_buying_score": score,
                    "evidence": f"Google Maps type '{key}': {why}",
                    "source": "Google Maps Types", "confidence": 0.95,
                    "verified": True}

    # 2. Website content & business description evidence (Before Name cues)
    if desc_clean.strip():
        # Cafe vs Restaurant check
        if "cafe" in desc_clean or "coffee shop" in desc_clean or "coffee house" in desc_clean:
            # Check if lodging/hotel is mentioned or in types to avoid false positive
            if "lodging" in types or "hotel" in name or "resort" in name:
                return {"division": "hotel", "coffee_buying_score": 90,
                        "evidence": "Description indicates hotel beverage/coffee operation",
                        "source": "Website/Description Evidence", "confidence": 0.85,
                        "verified": True}
            return {"division": "cafe", "coffee_buying_score": 95,
                    "evidence": "Description indicates cafe operations",
                    "source": "Website/Description Evidence", "confidence": 0.85,
                    "verified": True}
        
        # Check other specific keywords in description
        desc_keywords = [
            ("distributor", "distributor", 75, "Description indicates distribution operations"),
            ("wholesaler", "wholesaler", 75, "Description indicates wholesale operations"),
            ("supermarket", "supermarket", 75, "Description indicates supermarket grocery"),
            ("kirana", "retail_kirana", 65, "Description indicates kirana/grocery store"),
            ("office pantry", "office_pantry", 45, "Description indicates office pantry/refreshments"),
            ("manufacturing", "manufacturing", 50, "Description indicates manufacturing operations"),
            ("facility management", "facility_management", 45, "Description indicates facility services"),
            ("school", "school", 60, "Description indicates school canteen/mess"),
            ("college", "college", 60, "Description indicates college canteen/mess"),
            ("university", "college", 60, "Description indicates university canteen/mess"),
            ("gifting", "corporate_gifting", 70, "Description indicates gifting hampering"),
            ("private label", "private_label", 60, "Description indicates private label brand"),
            ("exporter", "exporter", 60, "Description indicates exporting operations"),
        ]
        for kw, division, score, why in desc_keywords:
            if kw in desc_clean:
                return {"division": division, "coffee_buying_score": score,
                        "evidence": why,
                        "source": "Website/Description Evidence", "confidence": 0.80,
                        "verified": True}

    # 3. Name keywords (Weak fallback only)
    for keywords, division, score, why in _NAME_HINTS:
        if any(_name_hit(k, name) for k in keywords):
            # Special check: a hotel restaurant or hotel cafe name should still be classified as hotel
            if division in ("restaurant", "cafe") and ("hotel" in name or "resort" in name or "lodging" in types):
                return {"division": "hotel", "coffee_buying_score": 85,
                        "evidence": f"Hotel food & beverage cue in name: {company}",
                        "source": "Business Name Heuristic", "confidence": 0.50,
                        "verified": False}
            return {"division": division, "coffee_buying_score": score,
                    "evidence": f"{why} (from business name)",
                    "source": "Business Name Heuristic", "confidence": 0.50,
                    "verified": False}

    # 4. Default to unknown (never default to searched segment)
    return {"division": "unknown",
            "coffee_buying_score": 0,
            "evidence": "No verified category or coffee buying evidence discovered.",
            "source": "Unclassified Default", "confidence": 0.0,
            "verified": False}


def resolve_queries(segment: str) -> list[str]:
    seg = (segment or "").lower().strip().replace(" ", "_").replace("/", "_")
    
    # Map synonyms/aliases
    aliases = {
        "distributor": "distributor",
        "wholesaler": "distributor",
        "modern_trade": "retail_kirana",
        "supermarket": "retail_kirana",
        "grocery_chain": "retail_kirana",
        "retail_kirana": "retail_kirana",
        "retail": "retail_kirana",
        "kirana": "retail_kirana",
        "kirana_store": "retail_kirana",
        "corporate_office": "corporate_office",
        "corporate": "corporate_office",
        "office_pantry": "corporate_office",
        "manufacturing": "manufacturing",
        "industrial": "manufacturing",
        "facility_management": "facility_management",
        "hotel": "hotel",
        "restaurant": "hotel",
        "cafe": "hotel",
        "horeca": "hotel",
        "hospital": "hospital",
        "school": "hospital",
        "college": "hospital",
        "university": "hospital",
        "education": "hospital",
        "government": "govt_canteen",
        "govt_canteen": "govt_canteen",
        "corporate_gifting": "corporate_gifting",
        "gifting": "corporate_gifting",
        "private_label": "corporate_gifting",
        "exporter": "corporate_gifting",
        "institutional_buyer": "corporate_gifting",
    }
    
    resolved_seg = aliases.get(seg, seg)
    if resolved_seg in SEARCH_PROFILES:
        return SEARCH_PROFILES[resolved_seg]
        
    # Sweeping / unknown / all categories
    queries: list[str] = []
    for profile in SEARCH_PROFILES.values():
        for q in profile:
            if q not in queries:
                queries.append(q)
    return queries


# Priority cities (Purity Beans market focus)
# Local-first: Abohar → Punjab → North India → National
PRIORITY_CITIES = [
    "Abohar", "Fazilka", "Sri Ganganagar", "Ferozepur", "Bathinda",
    "Ludhiana", "Amritsar", "Jalandhar", "Chandigarh", "Patiala",
    "Delhi", "Jaipur", "Bangalore", "Mumbai", "Hyderabad",
]

# City tiers for radius-based discovery
CITY_TIERS = {
    "local":     ["Abohar", "Fazilka", "Sri Ganganagar", "Ferozepur"],
    # Punjab, properly. Six cities was not a state sweep — it missed Mohali,
    # Moga, Pathankot and the industrial belt, which is most of the trade.
    # Ordered largest-first so a sweep cut short still covers the best markets.
    "punjab":    ["Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Bathinda",
                  "Mohali", "Pathankot", "Hoshiarpur", "Moga", "Phagwara",
                  "Batala", "Barnala", "Khanna", "Muktsar", "Faridkot",
                  "Kapurthala", "Sangrur", "Rajpura", "Zirakpur", "Malerkotla"],
    # Delhi NCR is its own market. It was folded into "north" alongside Jaipur,
    # Agra and Meerut, so asking for NCR searched three cities that are not NCR.
    "delhi_ncr": ["Delhi", "Gurgaon", "Noida", "Ghaziabad", "Faridabad",
                  "Greater Noida"],
    "north":     ["Delhi", "Gurgaon", "Noida", "Jaipur", "Dehradun", "Agra",
                  "Meerut", "Chandigarh"],
    "national":  ["Bangalore", "Mumbai", "Hyderabad", "Pune", "Chennai", "Kolkata"],
}

# Revenue tier estimates by segment
SEGMENT_VALUE_ESTIMATE = {
    # Demand-side consumers — annual instant-coffee spend (documented estimate).
    "govt_canteen":        300_000,   # PSU/railway/defence canteens — large headcount
    "facility_management": 260_000,   # manages many sites → aggregated demand
    "catering_contractor": 220_000,
    "industrial_canteen":  180_000,
    "education_mess":      160_000,
    "corporate_pantry":    150_000,
    "hospital":            140_000,
    "hotel_canteen":       130_000,
    "corporate_office":    120_000,
    "event_catering":      100_000,
    "guest_house":          90_000,
    # Resellers (buy to resell)
    "distributor":         180_000,
    "wholesaler":          300_000,
    "kirana_store":         60_000,
    "retail_chain":        200_000,
    "corporate_gifting":   240_000,
}


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return phone


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


# ── Google Maps Places API ────────────────────────────────────────────────────

def search_google_maps(
    query: str,
    city: str,
    api_key: Optional[str] = None,
    max_results: int = 20,
) -> list[dict]:
    """
    Search Google Maps Places API for leads.
    Returns list of normalized lead dicts.
    Requires GOOGLE_MAPS_API_KEY in env or passed directly.
    """
    key = api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        return _mock_google_maps_results(query, city)

    results = []
    search_term = f"{query} in {city} India"
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params={"query": search_term, "key": key, "language": "en"})
            data = resp.json()

            for place in data.get("results", [])[:max_results]:
                lead = {
                    "company": _clean_name(place.get("name", "")),
                    "city": city,
                    "lead_source": "Google Maps",
                    "phone": _normalize_phone(place.get("formatted_phone_number", "")),
                    "address": place.get("formatted_address", ""),
                    "rating": place.get("rating", 0),
                    "maps_rating": place.get("rating"),
                    "maps_reviews_count": place.get("user_ratings_total"),
                    "types": place.get("types", []),
                    "place_id": place.get("place_id", ""),
                    "contact_name": "",
                    "email": "",
                    "website": place.get("website", ""),
                    "latitude": place.get("geometry", {}).get("location", {}).get("lat"),
                    "longitude": place.get("geometry", {}).get("location", {}).get("lng"),
                }

                # Try to get details (phone, website)
                if place.get("place_id") and not lead["phone"]:
                    details = _get_place_details(client, place["place_id"], key)
                    lead.update(details)

                results.append(lead)

    except Exception as e:
        print(f"[Discovery] Google Maps error: {e}")

    return results


def _get_place_details(client: httpx.Client, place_id: str, key: str) -> dict:
    """
    Place Details. Requests the amenity fields as well as contact details:
    serves_breakfast is the signal Google actually reports, which turns the
    +30 breakfast weight from an inference ("it's a hotel, so probably") into
    verified evidence. Each call is billed, so callers should cache the result
    (see place_details_checked_at).
    """
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    try:
        resp = client.get(url, params={
            "place_id": place_id,
            "fields": ("name,formatted_phone_number,website,"
                       "international_phone_number,serves_breakfast,"
                       "serves_brunch,serves_lunch,dine_in,business_status,types"),
            "key": key,
        })
        payload = resp.json()
        result = payload.get("result", {})
        out = {
            "phone": _normalize_phone(
                result.get("international_phone_number") or
                result.get("formatted_phone_number", "")
            ),
            "website": result.get("website", ""),
            "business_status": result.get("business_status"),
            "types": result.get("types", []),
        }
        # Only report the amenity when Google actually returned it. A missing
        # field means "unknown", not False — recording False would fabricate a
        # negative signal.
        for amenity in ("serves_breakfast", "serves_brunch", "serves_lunch", "dine_in"):
            if amenity in result:
                out[amenity] = bool(result[amenity])
        return out
    except Exception:
        return {}


def resolve_place_id(company: str, city: str, api_key: str | None = None) -> str:
    """
    Find the Places place_id for a business we already hold. Needed to backfill
    leads discovered before place_id was persisted — without it their amenities
    can never be queried.
    """
    key = api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key or not (company or "").strip():
        return ""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                params={"input": f"{company} {city or ''}".strip(),
                        "inputtype": "textquery", "fields": "place_id",
                        "key": key})
            cands = resp.json().get("candidates", [])
            return cands[0].get("place_id", "") if cands else ""
    except Exception:
        return ""


def fetch_place_amenities(place_id: str, api_key: str | None = None) -> dict:
    """Amenity + contact detail for a known place_id."""
    key = api_key or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key or not place_id:
        return {}
    try:
        with httpx.Client(timeout=15) as client:
            return _get_place_details(client, place_id, key)
    except Exception:
        return {}


def _mock_google_maps_results(query: str, city: str) -> list[dict]:
    """
    Real-data-only: with no Google Maps API key we CANNOT discover real
    businesses, so we return nothing. Never fabricate company names, contacts,
    phones, or emails — an empty result is honest; invented leads are not.
    """
    return []


# ── IndiaMART (scraping / API placeholder) ───────────────────────────────────

def search_indiamart(query: str, city: str, max_results: int = 20) -> list[dict]:
    """
    Search IndiaMART for supplier leads.
    Uses their public search URL — no API key needed.
    Returns normalized lead dicts.
    """
    results = []
    search_url = "https://dir.indiamart.com/search.mp"

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-IN,en;q=0.9",
        }
        with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
            resp = client.get(search_url, params={"ss": query, "CatLGid": city})
            # Parse company names from the HTML
            companies = re.findall(
                r'class="companyname[^"]*"[^>]*>([^<]{3,80})<',
                resp.text
            )
            phones = re.findall(r'(\+91[\s-]?\d{5}[\s-]?\d{5})', resp.text)
            cities = re.findall(r'class="compAdd[^"]*"[^>]*>([^<]{3,50})<', resp.text)

            for i, company in enumerate(companies[:max_results]):
                results.append({
                    "company": _clean_name(company),
                    "city": cities[i] if i < len(cities) else city,
                    "phone": _normalize_phone(phones[i]) if i < len(phones) else "",
                    "email": "",
                    "contact_name": "",
                    "lead_source": "IndiaMART",
                    "website": "",
                })
    except Exception as e:
        print(f"[Discovery] IndiaMART error: {e}")

    # Real-data-only: if the scrape is blocked or empty, return nothing —
    # never fabricate placeholder companies/contacts.
    return results


# ── Orchestrator ──────────────────────────────────────────────────────────────

def discover_leads(
    segment: str,
    cities: Optional[list[str]] = None,
    sources: Optional[list[str]] = None,
    max_per_query: int = 10,
    origin_city: Optional[str] = None,
) -> dict:
    """
    Run discovery for a given segment across cities and sources.
    Returns normalized lead list + metadata.
    """
    if cities is None:
        cities = [origin_city] if origin_city else PRIORITY_CITIES[:5]
    if sources is None:
        sources = ["google_maps", "indiamart"]

    queries = resolve_queries(segment)
    # Categorized sweeps - if All Categories is active, we query multiple segment profiles.
    sweeping = (segment or "").lower().strip() in ("", "all", "any")
    per_city = 24 if sweeping else 2
    all_leads = []
    seen = set()

    for city in cities:
        for query in queries[:per_city]:
            if "google_maps" in sources:
                found = search_google_maps(query, city, max_results=max_per_query)
                for lead in found:
                    key = (lead["company"].lower(), city.lower())
                    if key not in seen:
                        seen.add(key)
                        lead["segment"] = segment
                        lead["estimated_annual_value"] = SEGMENT_VALUE_ESTIMATE.get(segment, 60_000)
                        all_leads.append(lead)

            if "indiamart" in sources:
                found = search_indiamart(query, city, max_results=max_per_query)
                for lead in found:
                    key = (lead["company"].lower(), city.lower())
                    if key not in seen:
                        seen.add(key)
                        lead["segment"] = segment
                        lead["estimated_annual_value"] = SEGMENT_VALUE_ESTIMATE.get(segment, 60_000)
                        all_leads.append(lead)

            time.sleep(0.1)  # polite delay

    return {
        "segment": segment,
        "cities_searched": cities,
        "queries_run": queries[:per_city],
        "leads_found": len(all_leads),
        "estimated_pipeline_value": len(all_leads) * SEGMENT_VALUE_ESTIMATE.get(segment, 60_000),
        "leads": all_leads,
        "discovered_at": datetime.utcnow().isoformat(),
    }


_SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}

_QTY_RE    = re.compile(r'(\d[\d,]*\s*(?:kg|g|MT|Ton|Bag|Pcs|Unit|Box|Case|Quintal|Dozen|Pack|Carton)s?)', re.I)
_TIME_RE   = re.compile(r'(\d+\s+(?:hour|day|week|month)s?\s+ago)', re.I)
_PHONE_RE  = re.compile(r'(\+91[\s-]?\d{5}[\s-]?\d{5}|\b[6-9]\d{9}\b)')


def _scrape_indiamart_buy(product: str, per_source: int) -> list[dict]:
    # buy-trade-leads/{slug}.html was retired by IndiaMART (404); use live search.
    url  = f"https://dir.indiamart.com/search.mp?ss={product.replace(' ', '+')}"
    try:
        with httpx.Client(timeout=15, headers={**_SCRAPE_HEADERS, "Referer": "https://www.indiamart.com/"}, follow_redirects=True) as c:
            html = c.get(url).text
        names = re.findall(r'class="[^"]*buyer-name[^"]*"[^>]*>([^<]{3,60})<', html)
        locs  = re.findall(r'class="[^"]*buyer-loc[^"]*"[^>]*>([^<]{2,50})<', html)
        prods = re.findall(r'class="[^"]*product-desc[^"]*"[^>]*>([^<]{5,150})<', html)
        qtys  = _QTY_RE.findall(html)
        times = _TIME_RE.findall(html)
        phones= _PHONE_RE.findall(html)
        leads = []
        for i, name in enumerate(names[:per_source]):
            leads.append({
                "buyer_name":     name.strip(),
                "city":           locs[i].strip()  if i < len(locs)  else "India",
                "product_needed": prods[i].strip() if i < len(prods) else product,
                "quantity":       qtys[i].strip()  if i < len(qtys)  else "On request",
                "posted_ago":     times[i].strip() if i < len(times) else "Recently",
                "phone":          _normalize_phone(phones[i]) if i < len(phones) else "",
                "source":         "IndiaMart",
                "source_url":     url,
            })
        return leads
    except Exception as e:
        print(f"[BuyLeads][IndiaMart] {e}")
        return []


def _scrape_tradeindia_buy(product: str, per_source: int) -> list[dict]:
    q   = product.replace(" ", "+")
    # /buyers/{q}.html was retired by TradeIndia (404); use live search endpoint.
    url = f"https://www.tradeindia.com/search.html?keyword={q}"
    try:
        with httpx.Client(timeout=15, headers={**_SCRAPE_HEADERS, "Referer": "https://www.tradeindia.com/"}, follow_redirects=True) as c:
            html = c.get(url).text
        # TradeIndia buyer listing patterns
        names  = re.findall(r'class="[^"]*comp[_-]?name[^"]*"[^>]*>([^<]{3,80})<', html)
        locs   = re.findall(r'class="[^"]*location[^"]*"[^>]*>([^<]{2,60})<', html)
        reqs   = re.findall(r'class="[^"]*desc[^"]*"[^>]*>([^<]{10,200})<', html)
        qtys   = _QTY_RE.findall(html)
        times  = _TIME_RE.findall(html)
        phones = _PHONE_RE.findall(html)
        leads  = []
        for i, name in enumerate(names[:per_source]):
            leads.append({
                "buyer_name":     name.strip(),
                "city":           locs[i].strip()  if i < len(locs)  else "India",
                "product_needed": reqs[i].strip()  if i < len(reqs)  else product,
                "quantity":       qtys[i].strip()  if i < len(qtys)  else "On request",
                "posted_ago":     times[i].strip() if i < len(times) else "Recently",
                "phone":          _normalize_phone(phones[i]) if i < len(phones) else "",
                "source":         "TradeIndia",
                "source_url":     url,
            })
        return leads
    except Exception as e:
        print(f"[BuyLeads][TradeIndia] {e}")
        return []


def _scrape_exportersindia_buy(product: str, per_source: int) -> list[dict]:
    q   = product.replace(" ", "+")
    # /buy-offers/{q}.htm was retired by ExportersIndia (404); use live search.
    url = f"https://www.exportersindia.com/search.php?term={q}"
    try:
        with httpx.Client(timeout=15, headers={**_SCRAPE_HEADERS, "Referer": "https://www.exportersindia.com/"}, follow_redirects=True) as c:
            html = c.get(url).text
        names  = re.findall(r'class="[^"]*company[_-]?name[^"]*"[^>]*>([^<]{3,80})<', html)
        locs   = re.findall(r'class="[^"]*city[^"]*"[^>]*>([^<]{2,60})<', html)
        reqs   = re.findall(r'class="[^"]*prodname[^"]*"[^>]*>([^<]{5,150})<', html)
        qtys   = _QTY_RE.findall(html)
        times  = _TIME_RE.findall(html)
        phones = _PHONE_RE.findall(html)
        leads  = []
        for i, name in enumerate(names[:per_source]):
            leads.append({
                "buyer_name":     name.strip(),
                "city":           locs[i].strip()  if i < len(locs)  else "India",
                "product_needed": reqs[i].strip()  if i < len(reqs)  else product,
                "quantity":       qtys[i].strip()  if i < len(qtys)  else "On request",
                "posted_ago":     times[i].strip() if i < len(times) else "Recently",
                "phone":          _normalize_phone(phones[i]) if i < len(phones) else "",
                "source":         "ExportersIndia",
                "source_url":     url,
            })
        return leads
    except Exception as e:
        print(f"[BuyLeads][ExportersIndia] {e}")
        return []


def _scrape_globallinker(product: str, per_source: int) -> list[dict]:
    q   = product.replace(" ", "+")
    url = f"https://globallinker.com/search?q={q}&type=requirement"
    try:
        with httpx.Client(timeout=15, headers=_SCRAPE_HEADERS, follow_redirects=True) as c:
            html = c.get(url).text
        names  = re.findall(r'class="[^"]*business[_-]?name[^"]*"[^>]*>([^<]{3,80})<', html)
        locs   = re.findall(r'class="[^"]*location[^"]*"[^>]*>([^<]{2,60})<', html)
        reqs   = re.findall(r'class="[^"]*requirement[^"]*"[^>]*>([^<]{10,200})<', html)
        leads  = []
        for i, name in enumerate(names[:per_source]):
            leads.append({
                "buyer_name":     name.strip(),
                "city":           locs[i].strip() if i < len(locs) else "India",
                "product_needed": reqs[i].strip() if i < len(reqs) else product,
                "quantity":       "On request",
                "posted_ago":     "Recently",
                "phone":          "",
                "source":         "GlobalLinker",
                "source_url":     url,
            })
        return leads
    except Exception as e:
        print(f"[BuyLeads][GlobalLinker] {e}")
        return []


def _scrape_justdial(product: str, per_source: int) -> list[dict]:
    """Justdial business search for buyers/distributors of the product."""
    q   = product.replace(" ", "-").lower()
    url = f"https://www.justdial.com/India/{q}-dealers/nct-10215973"
    try:
        with httpx.Client(timeout=15, headers={**_SCRAPE_HEADERS, "Referer": "https://www.justdial.com/"}, follow_redirects=True) as c:
            html = c.get(url).text
        names  = re.findall(r'class="[^"]*store-name[^"]*"[^>]*>([^<]{3,80})<', html)
        locs   = re.findall(r'class="[^"]*address[^"]*"[^>]*>([^<]{3,80})<', html)
        phones = _PHONE_RE.findall(html)
        leads  = []
        for i, name in enumerate(names[:per_source]):
            leads.append({
                "buyer_name":     name.strip(),
                "city":           locs[i].strip() if i < len(locs) else "India",
                "product_needed": f"Bulk buy / distribute: {product}",
                "quantity":       "On request",
                "posted_ago":     "Active listing",
                "phone":          _normalize_phone(phones[i]) if i < len(phones) else "",
                "source":         "JustDial",
                "source_url":     url,
            })
        return leads
    except Exception as e:
        print(f"[BuyLeads][JustDial] {e}")
        return []


# Source-specific brand colours used by the frontend
SOURCE_COLORS: dict[str, str] = {
    "IndiaMart":       "bg-orange-500/15 text-orange-400 border-orange-500/40",
    "TradeIndia":      "bg-blue-500/15 text-blue-400 border-blue-500/40",
    "ExportersIndia":  "bg-purple-500/15 text-purple-400 border-purple-500/40",
    "GlobalLinker":    "bg-teal-500/15 text-teal-400 border-teal-500/40",
    "JustDial":        "bg-yellow-500/15 text-yellow-500 border-yellow-500/40",
}


def discover_buy_leads(product: str = "instant coffee", max_results: int = 50) -> list[dict]:
    """
    Scrape pan-India buy leads from ALL major B2B portals in parallel.
    Sources: IndiaMart · TradeIndia · ExportersIndia · GlobalLinker · JustDial
    Falls back to realistic mock data per source if scraping is blocked.
    """
    import concurrent.futures

    per_source = max(8, max_results // 5)
    scrapers = [
        (_scrape_indiamart_buy,       product, per_source),
        (_scrape_tradeindia_buy,      product, per_source),
        (_scrape_exportersindia_buy,  product, per_source),
        (_scrape_globallinker,        product, per_source),
        (_scrape_justdial,            product, per_source),
    ]

    all_leads: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn, p, n): fn.__name__ for fn, p, n in scrapers}
        for fut in concurrent.futures.as_completed(futures, timeout=20):
            try:
                all_leads.extend(fut.result())
            except Exception as _exc:
                # Swallowed on purpose — this path must not break the
                # caller — but never silently: a failure with no name is
                # how the category engine fell back for hours unnoticed.
                _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Assign urgency by recency keywords and rank
    high_kw = re.compile(r'hour|today|urgent|immediate', re.I)
    med_kw  = re.compile(r'day|week', re.I)
    for i, l in enumerate(all_leads):
        t = l.get("posted_ago", "")
        if high_kw.search(t) or i < 6:
            l["urgency"] = "HIGH"
        elif med_kw.search(t) or i < 20:
            l["urgency"] = "MEDIUM"
        else:
            l["urgency"] = "LOW"

    # Fall back to realistic mock if total real results too thin
    if len(all_leads) < 5:
        return _mock_buy_leads(product)

    return all_leads[:max_results]


def _mock_buy_leads(product: str) -> list[dict]:
    """
    Real-data-only: when every buy-lead portal is blocked we CANNOT surface real
    buyers, so we return nothing. Never fabricate buyer names, cities, or product
    requirements — an empty result is honest; invented buy-leads are not.
    """
    return []


def _is_placeholder_phone(value: str) -> bool:
    """
    Fabricated number check, applied to anything discovery wants to STORE.

    Kept local to avoid importing the API layer. Without this, rediscovery
    happily re-wrote a placeholder that had been deliberately quarantined:
    "Chhabra Bartan Store" had +919700000076 nulled as fabricated, and the next
    discovery run restored it from the same bad source — the enricher's
    re-fabrication defect reappearing on the discovery path. Bad data must be
    rejected at every write, not just cleaned once.
    """
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return False
    core = digits[-10:] if len(digits) >= 10 else digits
    if len(set(core)) <= 2:                       # 8888888888, 9999999999
        return True
    return bool(re.search(r"(12345|00000|11111)", core))


def _identity_key(name: str, city: str = "") -> str:
    """
    Normalised identity for matching a business across rediscoveries. Strips
    punctuation, legal suffixes and a trailing city, so "Suto Cafe, Bathinda"
    and "Suto Cafe Bathinda." resolve to the same business.

    The CITY IS PART OF THE KEY, not just stripped from the name. "Shaina cafe"
    in Fazilka and "Shaina Cafe Bathinda" in Bathinda are two different
    businesses that happen to share a name — keying on the name alone would
    merge them and destroy one of their histories.
    """
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    s = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|co|company|and|the)\b", " ", s)
    c = re.sub(r"[^a-z0-9]", "", (city or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    if c and s.endswith(f" {c}"):
        s = s[: -len(c) - 1].strip()
    return f"{s}|{c}"


def save_discovered_leads(leads: list[dict], db, origin_city: Optional[str] = None) -> dict:
    """
    Insert discovered businesses, or UPDATE the ones we already know.

    Business Memory: a business is a permanent record, not a fresh lead each
    time it is found. Rediscovering it must refresh what we know and keep the
    existing history — previously an existing company was simply skipped, so a
    business whose phone, website or rating changed between searches kept stale
    data forever, and any near-miss in the name (punctuation, a trailing city,
    "Pvt Ltd") created a duplicate with none of the interaction history.

    Matching is by Google place_id first (a stable identity), then by
    normalised name + city.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.revenue_scoring import score_lead

    from app.services.evidence_collector import disqualifying_signal

    inserted = 0
    skipped = 0
    updated = 0
    # Businesses refused at the gate, and why. Returned to the caller so the
    # founder can see what discovery threw away — a silent filter is how a real
    # buyer gets dropped without anyone noticing.
    rejected: list[dict] = []
    seen_in_batch: set[str] = set()   # same company can appear in several city queries

    # Origin coordinates
    origin_lat, origin_lng = None, None
    if origin_city and origin_city.lower().strip() in CITY_COORDINATES:
        origin_lat, origin_lng = CITY_COORDINATES[origin_city.lower().strip()]

    for raw in leads:
        company = _clean_name(raw.get("company", ""))
        if not company:
            skipped += 1
            continue

        city_raw = raw.get("city", "") or ""
        ident = _identity_key(company, city_raw)
        if ident in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(ident)

        # Resolve coordinates, state and distance
        lat = raw.get("latitude") or raw.get("lat")
        lng = raw.get("longitude") or raw.get("lng")
        if lat is None or lng is None:
            c_clean = city_raw.lower().strip()
            if c_clean in CITY_COORDINATES:
                lat, lng = CITY_COORDINATES[c_clean]

        state = "Punjab"  # Default fallback state
        addr = raw.get("address") or ""
        if "punjab" in addr.lower():
            state = "Punjab"
        elif "haryana" in addr.lower():
            state = "Haryana"
        elif "delhi" in addr.lower():
            state = "Delhi"
        elif "rajasthan" in addr.lower():
            state = "Rajasthan"
        elif city_raw.lower().strip() in ("chandigarh", "mohali", "panchkula", "zirakpur", "kharar", "abohar", "bathinda", "ludhiana", "amritsar", "jalandhar", "patiala"):
            state = "Punjab"

        distance = None
        if origin_lat is not None and origin_lng is not None and lat is not None and lng is not None:
            distance = calculate_distance(origin_lat, origin_lng, lat, lng)

        # ── Identity resolution: place_id, then normalised name + city ──
        pid = (raw.get("place_id") or "").strip()
        existing = None
        if pid:
            existing = db.query(B2BLead).filter(B2BLead.place_id == pid).first()
        if existing is None:
            for cand in db.query(B2BLead).filter(
                    B2BLead.city.ilike(f"%{city_raw}%") if city_raw else True).all():
                if _identity_key(cand.company or "", cand.city or "") == ident:
                    existing = cand
                    break

        if existing is not None:
            # Refresh what we now know. Never overwrite a real stored value with
            # an empty one.
            refreshed = []
            _newphone = _normalize_phone(raw.get("phone", "") or "")
            if _newphone and _is_placeholder_phone(_newphone):
                _newphone = ""          # never restore a fabricated number
            for field, val in (("phone", _newphone),
                               ("website", raw.get("website") or ""),
                               ("address", raw.get("address") or ""),
                               ("place_id", pid),
                               ("state", state),
                               ("latitude", lat),
                               ("longitude", lng),
                               ("distance_from_origin", distance),
                               ("origin_city", origin_city)):
                # str() before strip(): this list mixes text fields with numeric
                # ones (latitude, longitude, distance), and the existing VALUE is
                # what gets stripped — so a stored float raised AttributeError
                # and killed the whole city's import. The isinstance guard that
                # was added sits after the strip, so it never got the chance to
                # fire. 174 Ludhiana businesses were lost to this on every run.
                if val and not str(getattr(existing, field, "") or "").strip() \
                        and not isinstance(val, float):
                    setattr(existing, field, val)
                    refreshed.append(field)
                elif isinstance(val, float) and getattr(existing, field, None) is None:
                    setattr(existing, field, val)
                    refreshed.append(field)
            for field, val in (("maps_rating", raw.get("maps_rating")),
                               ("maps_reviews_count", raw.get("maps_reviews_count")),
                               ("business_status", raw.get("business_status"))):
                if val is not None and getattr(existing, field, None) != val:
                    setattr(existing, field, val)
                    refreshed.append(field)
            if raw.get("types"):
                existing.maps_types = ",".join(str(t) for t in raw["types"])[:400]
                refreshed.append("maps_types")
            existing.last_updated = datetime.utcnow()
            # Facts changed, so the previous assessment is stale.
            if refreshed:
                existing.intelligence_status = "NOT_STARTED"
            db.add(WorkflowEvent(
                lead_id=existing.id, event_type="BUSINESS_REDISCOVERED",
                actor="SYSTEM", channel="discovery",
                payload={"source": raw.get("lead_source") or "UNKNOWN_SOURCE",
                         "refreshed_fields": refreshed,
                         "matched_by": "place_id" if pid and existing.place_id == pid else "name+city"},
                occurred_at=datetime.utcnow()))
            try:
                db.commit()
                updated += 1
            except Exception:
                db.rollback()
                skipped += 1
            continue

        seg = raw.get("segment", "corporate")
        _rating = raw.get("maps_rating")
        _reviews = raw.get("maps_reviews_count")
        _types = raw.get("types") or []

        # Classify from what the business ACTUALLY is, not from the segment we
        # happened to be searching.
        cls = classify_business(_types, company, fallback_segment=seg)
        real_div = cls["division"]

        # ── Does this business plausibly buy coffee at all? ──
        _dq = disqualifying_signal(company, _types)
        if _dq:
            rejected.append({"company": company, "city": city_raw,
                             "reason": f"{_dq[0]}: {_dq[1]}"})
            skipped += 1
            continue
        if not cls.get("verified") and cls.get("coffee_buying_score", 0) <= 20:
            rejected.append({"company": company, "city": city_raw,
                             "reason": "NO_BUYING_SIGNAL: category taken from the "
                                       "search segment, not from the business"})
            skipped += 1
            continue

        from app.services.revenue_engine import estimate_annual_value as _est
        _value = raw.get("estimated_annual_value") or _est(
            real_div, raw.get("city", ""), reviews=_reviews, rating=_rating)
        
        lead = B2BLead(
            company=company,
            contact_name=raw.get("contact_name", ""),
            email=raw.get("email", ""),
            phone=("" if _is_placeholder_phone(raw.get("phone", "") or "")
                   else raw.get("phone", "")),
            whatsapp_number=("" if _is_placeholder_phone(raw.get("phone", "") or "")
                             else raw.get("phone", "")),
            city=raw.get("city", ""),
            address=raw.get("address", ""),
            website=raw.get("website", ""),
            lead_source=(raw.get("lead_source") or "UNKNOWN_SOURCE"),
            division=real_div,
            industry=real_div.replace("_", " ").title(),
            status="DISCOVERED",
            estimated_value=_value,
            maps_rating=_rating,
            maps_reviews_count=_reviews,
            maps_types=",".join(str(t) for t in _types)[:400] or None,
            place_id=(raw.get("place_id") or None),
            serves_breakfast=raw.get("serves_breakfast"),
            business_status=raw.get("business_status"),
            division_source=cls.get("source"),
            division_confidence=cls.get("confidence", 0.25),
            division_verified=cls.get("verified", False),
            state=state,
            latitude=lat,
            longitude=lng,
            distance_from_origin=distance,
            origin_city=origin_city,
            searched_category=seg,
        )

        result = score_lead(lead)
        lead.segment = result["segment"]
        lead.final_score = result["final_score"]
        lead.revenue_tier = result["revenue_tier"]
        lead.estimated_annual_value = result["estimated_annual_value"]
        lead.coffee_buying_score = cls.get("coffee_buying_score", 0)
        lead.coffee_buying_evidence = cls.get("evidence", "")

        db.add(lead)
        try:
            db.commit()
            inserted += 1
        except Exception:
            db.rollback()
            skipped += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped,
            "rejected_not_coffee_buyers": len(rejected), "rejected": rejected[:50]}
