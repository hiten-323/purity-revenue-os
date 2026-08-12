"""
Evidence collection — writes Truth Layer rows from real sources.

Collects facts only. It never computes a score, classification or next action;
those are derived on read by services/opportunity_intelligence.py.

Includes the DISQUALIFIERS, which had no representation anywhere before: a
permanently-closed business, a residential address, or a business with neither
website nor reviews could previously score positively on category alone and
reach the founder's call queue.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.services.opportunity_intelligence import SOURCE_CONFIDENCE

# signal_type -> (positive weight, negative weight, default source)
SIGNAL_WEIGHTS = {
    # Positive
    "TENDER_EXISTS":         (50, 0, "Government Tender Portal"),
    "BREAKFAST_SERVICE":     (30, 0, "Google Places API"),
    "PANTRY_MENTION":        (20, 0, "Official Website"),
    "BEVERAGE_TRADE":        (20, 0, "Google Maps"),
    "BEVERAGE_LICENSE":      (20, 0, "FSSAI"),
    "EMPLOYEE_COUNT_LARGE":  (25, 0, "Official Website"),
    "EMPLOYEE_COUNT_MEDIUM": (10, 0, "Official Website"),
    "EMPLOYEE_COUNT_SMALL":  (5,  0, "Official Website"),
    "CURRENT_BRAND_MENTION": (10, 0, "Official Website"),
    "HIGH_REVIEW_COUNT":     (10, 0, "Google Maps"),
    "VERIFIED_PHONE":        (5,  0, "Contact Enrichment"),
    "CORPORATE_BOOKINGS":    (15, 0, "Official Website"),
    "FOOD_SERVICE_CATEGORY": (15, 0, "Google Maps"),
    # Negative — disqualifiers
    "PERMANENTLY_CLOSED":    (0, 100, "Google Places API"),
    "RESIDENTIAL_ADDRESS":   (0, 100, "Google Maps"),
    "WRONG_CATEGORY":        (0, 50,  "Google Maps"),
    "NO_WEBSITE_NO_REVIEWS": (0, 30,  "Google Maps"),
    "MICRO_BUSINESS":        (0, 20,  "Official Website"),
}

# Categories that cannot plausibly buy coffee in commercial volume.
_WRONG_CATEGORY_TYPES = {
    "beauty_salon", "hair_care", "spa", "gym", "clothing_store", "shoe_store",
    "jewelry_store", "car_repair", "car_dealer", "gas_station", "bank", "atm",
    "pharmacy", "hardware_store", "furniture_store", "electronics_store",
    "movie_theater", "cemetery", "funeral_home", "veterinary_care",
    "real_estate_agency", "moving_company", "painter", "plumber", "roofing_contractor",
    # Trades and services with no pantry or resale demand for instant coffee.
    # general_contractor covered construction firms, land surveyors and interior
    # fabricators that were sitting in the verified set purely because Google
    # confirmed they exist — existing is not the same as buying coffee.
    "general_contractor", "electrician", "locksmith", "lawyer",
    "dentist", "doctor", "physiotherapist", "primary_school", "driving_school",
    "travel_agency", "taxi_stand", "car_rental", "parking",
    "book_store", "pet_store", "florist", "bicycle_store", "hindu_temple",
    "mosque", "church", "police", "fire_station", "courthouse", "embassy",
    # NOT "storage" and NOT "laundry": Google lists `storage` on warehouses,
    # which is exactly what a distributor operates — including it deleted
    # "Mukand Lal Ude Chand - Nestle Distributor", the strongest coffee lead in
    # the database. Google also tags mixed general stores with `laundry`.
    # A secondary service type must never outweigh the primary trade.
}

# Trade words that identify a non-coffee business even when Google gives no
# useful type. Matched as whole words, so "steel" does not catch "Steelworks
# Cafe" and "estate" does not catch a hotel with Estate in its name.
_NON_COFFEE_TRADES = (
    "construction", "constructions", "surveying", "surveyor", "builder",
    "builders", "realtor", "interior", "interiors", "fabricator", "fabricators",
    "architect", "architects", "granite", "marble", "sanitary", "plywood",
    "scrap", "petrol", "diesel", "welding", "borewell", "nursery",
)
_WRONG_CATEGORY_NAMES = (
    # "movers"/"packers" are matched as bare words rather than the full phrase
    # "packers and movers": a real listing reads "Agarwal Packes And Movers" —
    # misspelled on Google Maps — and the phrase match missed it while catching
    # "Bathinda Packers And Movers". Deliberately NOT including "logistics" or
    # "courier": those run offices and can plausibly buy pantry coffee, whereas
    # a moving service never buys in commercial volume.
    # Beauty / cosmetics ("shingar" is Hindi for cosmetics/adornment) and
    # agri-processing (a kinnow waxing-and-grading plant packs citrus, it does
    # not buy instant coffee). Deliberately NOT "karyana"/"kirana"/"general
    # store"/"grain market"/"traders": those are real retail and wholesale
    # channels that stock packaged coffee — the founder grouped them with the
    # bad rows because of the identical fake scores, not because they cannot buy.
    "salon", "parlour", "parlor", "beauty", "shingar", "cosmetic", "waxing",
    "nail art", "boutique", "tailor", "gym", "fitness", "yoga",
    "movers", "packers", "car wash", "automobile",
    "tyre", "cement", "shuttring", "shuttering", "hardware", "paints",
    "opticals", "optician", "jewellers", "jewellery", "clinic", "dental",
    # Utensils / crockery: "bartan" is Hindi for utensils. A kitchenware shop
    # sells pots and pans, not coffee. "Bathinda Crockery and Bartan Store" was
    # caught by its Maps type; "Chhabra Bartan Store" had none and slipped
    # through as a distributor default.
    "bartan", "crockery", "kitchenware", "utensil",
)
# "Gali"/"Mohalla"/"Colony" alone were dropped — they name a lane or locality,
# which is how most small-town Indian shop addresses are written, not evidence
# the address is a private home. "Raju Karyana Store, Chandigarh Mohalla" is a
# real shop on a real street; the old pattern read every such address as
# residential. Kept to explicit dwelling-unit markers, which are what actually
# distinguish a home from a shop.
_RESIDENTIAL_RE = re.compile(r"\b(house no\.?|h\.?\s?no\.?\s?\d|flat no\.?|plot no\.?)\b", re.I)


_RETAIL_POSITIVE = ("general store", "karyana", "kiryana", "kirana",
                    "provision", "grocery", "grocer", "supermarket",
                    "super market", "departmental", "super store", "mart")


def disqualifying_signal(company: str, maps_types=None) -> tuple[str, str] | None:
    """
    Is this business disqualified as a coffee buyer on category alone?

    Returns (reason, matched_term) or None. This is the SINGLE place that answers
    the question — assess_lead() below uses it, and so does discovery, which
    previously did not ask at all. That was the whole defect: the vocabulary here
    already listed "kitchenware", so the Truth Layer knew CBS Kitchenware was
    wrong-category while discovery inserted it anyway and the Approval Center
    showed it as a Rs 1.8L opportunity. Knowing and acting were in different
    modules.

    A genuine retail term wins over a disqualifier, for the reason documented at
    _RETAIL_POSITIVE: a general store that also sells crockery is still a shop
    that stocks packaged coffee.
    """
    name = (company or "").lower()
    types = [str(t).lower() for t in (maps_types or [])]

    if any(t in name for t in _RETAIL_POSITIVE):
        return None

    # A QUALIFYING Maps type outranks a disqualifying one, because Google lists
    # everything a property contains, not what it is: "HOTEL PRADOE" comes back
    # as lodging + gym + spa. Checking disqualifiers first threw away a hotel —
    # and a hotel with a gym and a spa is a better coffee buyer than one without,
    # not a worse one. Same failure the "crockery" rule already had on retail.
    from app.services.lead_discovery import _MAPS_TYPE_MAP
    if any(k in types for k, *_ in _MAPS_TYPE_MAP):
        return None

    hit = next((t for t in types if t in _WRONG_CATEGORY_TYPES), None)
    if hit:
        return ("WRONG_CATEGORY", hit)
    hit = next((k for k in _WRONG_CATEGORY_NAMES if k in name), None)
    if hit:
        return ("WRONG_CATEGORY", hit)
    hit = next((t for t in _NON_COFFEE_TRADES
                if re.search(rf"\b{re.escape(t)}\b", name)), None)
    if hit:
        return ("WRONG_CATEGORY", hit)
    return None


def _row(lead_id: int, signal: str, value: str, source: str | None = None,
         source_url: str | None = None, verified: bool = False):
    from app.models.models import LeadEvidence
    pos, neg, default_source = SIGNAL_WEIGHTS[signal]
    src = source or default_source
    return LeadEvidence(
        lead_id=lead_id, signal_type=signal, value=str(value)[:300],
        source=src, source_url=source_url, collected_at=datetime.utcnow(),
        confidence=SOURCE_CONFIDENCE.get(src, 0.5), verified=verified,
        weight_positive=pos, weight_negative=neg,
    )


def collect_for_lead(lead, db, site_text: str = "") -> list:
    """
    Build evidence rows for one lead from facts already held plus optional
    website text. Replaces this lead's existing evidence so a re-run reflects
    current facts rather than stacking duplicates.
    """
    from app.models.models import LeadEvidence
    from datetime import datetime as _dt

    db.query(LeadEvidence).filter(LeadEvidence.lead_id == lead.id).delete()
    lead.evidence_collected_at = _dt.utcnow()
    rows = []
    types = [t.strip().lower() for t in (getattr(lead, "maps_types", "") or "").split(",") if t.strip()]
    name = (lead.company or "").lower()
    # Tri-state, deliberately not coerced to 0. maps_reviews_count is None for
    # every lead we have never looked up on Google Maps — 582 of them, this
    # entire phone-only cohort. Treating None as 0 was the exact bug already
    # fixed once for serves_breakfast, reintroduced here: NO_WEBSITE_NO_REVIEWS
    # then fired on every unchecked lead as if Maps had confirmed zero reviews,
    # producing hundreds of false disqualifications for real shops that were
    # simply never looked up.
    maps_checked = getattr(lead, "maps_reviews_count", None) is not None or getattr(lead, "maps_rating", None) is not None
    reviews = getattr(lead, "maps_reviews_count", None) or 0
    website = (lead.website or "").strip()

    # `protected` shields a coffee-buying division from a MAPS-TYPE false
    # positive only — Google tags a distributor's warehouse `storage` and a
    # general store's side-counter `laundry`, and trusting those deleted a
    # Nestle distributor and a top-ranked general store.
    #
    # It must NOT shield against the explicit NAME-based checks below. division
    # is frequently just a seed default with no real evidence behind it — for
    # 132 leads it reads "distributor" with empty maps_types, including
    # "Manokamna Beauty Centre" and "Sheetal Shingar Centre" (shingar = Hindi
    # for cosmetics). A business literally named "Beauty Centre" is stronger,
    # more direct evidence of what it is than a division field that was never
    # actually verified — so the curated name/trade-word lists always run,
    # regardless of protected.
    # Only a VERIFIED division earns protection. division is derived, and for
    # 132 leads it is just the search-segment default — trusting it blindly is
    # what let "Manokamna Beauty Centre" sit in the queue as a distributor.
    # A Google-Maps-confirmed category (verified=True) can shield against a
    # secondary type; a guessed one cannot shield against anything.
    _div = (getattr(lead, "division", "") or "").lower()
    _div_verified = bool(getattr(lead, "division_verified", False))
    protected = _div_verified and _div in (
        "distributor", "wholesaler", "grocery", "kirana_store",
        "retail_chain", "horeca", "hotel_canteen",
        "corporate_pantry", "catering_contractor")

    # A name that carries a genuine coffee-retail term is a coffee channel even
    # when it also mentions something on the disqualifier list. "Gopal General
    # Store & Crockery House" is a general store that happens to sell pots — it
    # stocks packaged coffee like any kirana. Without this, the "crockery" word
    # rejected a real retail outlet. A PURE utensils shop ("Chhabra Bartan
    # Store", "CBS Kitchenware") carries none of these terms and is still caught.
    retail_channel = any(t in name for t in _RETAIL_POSITIVE)

    # ── Disqualifiers first: they can end the assessment on their own ──
    if (getattr(lead, "business_status", "") or "").upper() == "CLOSED_PERMANENTLY":
        rows.append(_row(lead.id, "PERMANENTLY_CLOSED", "Closed permanently", verified=True))
    if (not protected) and any(t in _WRONG_CATEGORY_TYPES for t in types):
        hit = next(t for t in types if t in _WRONG_CATEGORY_TYPES)
        rows.append(_row(lead.id, "WRONG_CATEGORY", hit, verified=True))
    elif (not retail_channel) and any(k in name for k in _WRONG_CATEGORY_NAMES):
        hit = next(k for k in _WRONG_CATEGORY_NAMES if k in name)
        rows.append(_row(lead.id, "WRONG_CATEGORY", hit, source="Business Name Heuristic"))
    elif not retail_channel:
        _trade = next((t for t in _NON_COFFEE_TRADES
                       if re.search(rf"\b{re.escape(t)}\b", name)), None)
        if _trade:
            rows.append(_row(lead.id, "WRONG_CATEGORY", _trade,
                             source="Business Name Heuristic"))
    # Only a claim if Maps was actually queried and confirmed zero reviews.
    # Without maps_checked this fired on every lead we never looked up.
    if not website and maps_checked and not reviews:
        rows.append(_row(lead.id, "NO_WEBSITE_NO_REVIEWS", "no web presence or reviews", verified=True))
    addr = (lead.address or "")
    # Address disqualifier never overrides a real trade signal: a distributor
    # or grocer operating from a shop on a named lane ("Chandigarh Mohalla",
    # "Gali", "Colony") is not evidence of a residence — those are just how
    # small-town Indian addresses are written, and this fired on "Raju Karyana
    # Store" for exactly that reason. Only fires without a positive category.
    if (addr and _RESIDENTIAL_RE.search(addr) and not website and reviews < 5
            and not protected):
        rows.append(_row(lead.id, "RESIDENTIAL_ADDRESS", addr[:120]))

    # ── Positive signals ──
    if getattr(lead, "serves_breakfast", None) is True:
        rows.append(_row(lead.id, "BREAKFAST_SERVICE", "serves_breakfast=true",
                         source="Google Places API", verified=True))
    div = (lead.division or "").lower()
    if div in ("distributor", "wholesaler"):
        rows.append(_row(lead.id, "BEVERAGE_TRADE", f"category={div}", verified=True))
    elif div in ("grocery", "kirana_store", "retail_chain"):
        rows.append(_row(lead.id, "BEVERAGE_TRADE", f"category={div}", verified=True))
    if div in ("horeca", "hotel_canteen", "catering_contractor", "event_catering"):
        rows.append(_row(lead.id, "FOOD_SERVICE_CATEGORY", f"category={div}", verified=True))
    if reviews >= 100:
        rows.append(_row(lead.id, "HIGH_REVIEW_COUNT", f"{reviews} reviews", verified=True))
    if (lead.phone or "").strip() and (getattr(lead, "phone_source", "") or "").strip():
        rows.append(_row(lead.id, "VERIFIED_PHONE", lead.phone,
                         source="Contact Enrichment", verified=True))

    if site_text:
        url = website or None
        if re.search(r"\b(pantry|cafeteria|canteen|break\s?room)\b", site_text, re.I):
            rows.append(_row(lead.id, "PANTRY_MENTION", "pantry/cafeteria on site",
                             source="Official Website", source_url=url, verified=True))
        if re.search(r"\b(coffee|espresso|cappuccino)\b", site_text, re.I):
            rows.append(_row(lead.id, "CURRENT_BRAND_MENTION", "coffee referenced on site",
                             source="Official Website", source_url=url, verified=True))
        if re.search(r"\b(corporate booking|banquet|conference hall|conference room)\b", site_text, re.I):
            rows.append(_row(lead.id, "CORPORATE_BOOKINGS", "corporate/banquet facilities",
                             source="Official Website", source_url=url, verified=True))
        m = re.search(r"\b(\d{2,5})\+?\s*(?:employees|staff|team members)\b", site_text, re.I)
        if m:
            n = int(m.group(1))
            sig = ("EMPLOYEE_COUNT_LARGE" if n > 200 else
                   "EMPLOYEE_COUNT_MEDIUM" if n >= 50 else
                   "EMPLOYEE_COUNT_SMALL" if n >= 10 else "MICRO_BUSINESS")
            rows.append(_row(lead.id, sig, f"{n} employees",
                             source="Official Website", source_url=url, verified=True))

    for r in rows:
        db.add(r)
    return rows
