"""
Centralized Category & Negative Keyword Classification Engine
Purity Beans Founder Revenue OS

Strictly separates food, beverage, hospitality, gifting, and FMCG buyers
from non-coffee / non-food industries (agricultural fertilizers, hardware,
automobile parts, medical supplies, electricals, textiles, machinery, etc.).
"""
from __future__ import annotations

import re
from typing import Any

# Explicit food/beverage/hospitality terms that override generic trader heuristics
_POSITIVE_FOOD_RE = re.compile(
    r"\b("
    r"coffee|cafe|cafes|espresso|roast|roasters|roastery|brew|brews|brewery|tea|chai|beverage|beverages|"
    r"hotel|resort|resorts|inn|palace|suites|residency|lodging|guest\s*house|"
    r"restaurant|restaurants|restro|dhaba|bistro|diner|eatery|bhojnalaya|rasoi|canteen|cafeteria|mess|tiffin|"
    r"bakery|bakers|bakehouse|cake|cakes|pastry|patisserie|confectionery|sweets|sweet|halwai|"
    r"caterer|caterers|catering|banquet|banquets|"
    r"gifting|gift|gifts|hampers|hamper|"
    r"grocery|kirana|karyana|kiryana|parchun|supermarket|hypermarket|departmental\s*store|mart|provisions|dry\s*fruits|fmcg"
    r")\b",
    re.I
)

# Negative / Non-Coffee Disqualification Rules
DISQUALIFY_RULES = [
    # 1. Agriculture / Fertilizer / Pesticides / Seeds / Mandi Commission
    (
        re.compile(
            r"\b(fertilizer|fertilizers|pesticide|pesticides|insecticide|insecticides|"
            r"fungicide|fungicides|urea|potash|dap|kisan\s*seva|kheti|arhtiya|aarhti|"
            r"commission\s*agent|seeds\s*store|krishi|crop\s*care|agrochemical|agrochemicals|agro\s*chemicals)\b",
            re.I
        ),
        "AGRICULTURE_FERTILIZER",
        "Agricultural fertilizer, pesticide, seeds, or mandi commission agent (non-coffee)"
    ),
    # 2. Hardware / Construction / Sanitary / Paint / Plywood / Timber / Cement
    (
        re.compile(
            r"\b(cement|plywood|timber|sanitary|sanitaryware|hardware|pipe|pipes|steels?|"
            r"iron\s*store|paints?|asian\s*paints|berger|marble|tiles|granite|building\s*materials?|"
            r"glass\s*house|ply\s*house|shuttering|hardware\s*store)\b",
            re.I
        ),
        "HARDWARE_CONSTRUCTION",
        "Hardware, sanitaryware, paint, cement, or building material trader"
    ),
    # 3. Automobile / Tyres / Lubricants / Batteries / Mechanics / Tractors
    (
        re.compile(
            r"\b(tyres?|tires?|auto\s*parts|automobiles?|motors?|lubricants?|mobil\s*oil|"
            r"batteries|battery|tractors?|spare\s*parts|motorcycle|scooter|bike\s*point|car\s*accessories)\b",
            re.I
        ),
        "AUTOMOBILE_SPARE_PARTS",
        "Automobile, tyre, battery, tractor, or machinery spare parts dealer"
    ),
    # 4. Electricals / Electronics / Appliances / Solar / Mobile phones
    (
        re.compile(
            r"\b(electricals?|electronics?|electric\s*store|appliances?|wires|cables|solar|"
            r"inverters?|switchgear|mobile\s*world|cell\s*care|lighting)\b",
            re.I
        ),
        "ELECTRICAL_ELECTRONICS",
        "Electrical, electronic, appliance, solar, or mobile phone dealer"
    ),
    # 5. Medical / Pharma / Chemist / Surgical / Dental / Diagnostics
    (
        re.compile(
            r"\b(pharma|pharmaceuticals?|chemists?|druggists?|medicines|medical\s*hall|"
            r"medical\s*store|surgicals?|dental|diagnostics?|pathology|opticals?|eyecare)\b",
            re.I
        ),
        "MEDICAL_PHARMA",
        "Pharmacy, chemist, clinic, diagnostic lab, or medical supplies store"
    ),
    # 6. Textiles / Garments / Footwear / Jewelry
    (
        re.compile(
            r"\b(textiles?|garments?|cloth\s*house|cloth\s*store|fabrics?|handloom|sarees?|"
            r"suiting|shirting|jewellers?|jewellery|footwear|shoes?|boot\s*house)\b",
            re.I
        ),
        "TEXTILE_GARMENTS_JEWELRY",
        "Textile, garment, saree, footwear, or jewelry merchant"
    ),
    # 7. Industrial Machinery / Packaging / Chemicals / Scrap
    (
        re.compile(
            r"\b(corrugated|packaging\s*material|machinery|industrial\s*solutions|chemicals?|"
            r"scrap\s*traders?|polythene|plastic\s*works|printers?|printing\s*press|offset)\b",
            re.I
        ),
        "INDUSTRIAL_PACKAGING_CHEMICALS",
        "Industrial machinery, printing, packaging, scrap, or chemical dealer"
    ),
    # 8. Real estate / Transport / Tours & Travels / Stock Trading
    (
        re.compile(
            r"\b(properties|property\s*dealer|realtors?|real\s*estate|transporters?|"
            r"transport\s*company|logistics|courier|packers\s*and\s*movers|tour\s*and\s*travels|trading\s*academy)\b",
            re.I
        ),
        "TRANSPORT_REAL_ESTATE",
        "Real estate, transport, logistics, travel agency, or financial academy"
    ),
]


def is_disqualified_non_coffee(
    company: str | None,
    address: str | None = None,
    description: str | None = None,
    maps_types: list[str] | str | None = None,
) -> tuple[bool, str, str]:
    """
    Evaluates whether a lead belongs to an ineligible non-coffee / non-food category.

    Returns:
        (is_disqualified: bool, tag: str, reason: str)
    """
    comp = (company or "").strip()
    addr = (address or "").strip()
    desc = (description or "").strip()

    # If business name has clear food/beverage/hospitality keywords, it is safe
    if _POSITIVE_FOOD_RE.search(comp):
        return False, "", ""

    # Check negative rules against company name and description
    for pat, tag, reason in DISQUALIFY_RULES:
        if pat.search(comp):
            return True, tag, reason
        if desc and pat.search(desc):
            return True, tag, reason

    # Mandi / Grain Market address check: generic traders in agricultural grain market
    if addr and ("grain market" in addr.lower() or "new grain market" in addr.lower() or "timber market" in addr.lower()):
        if any(w in comp.lower() for w in ["trader", "trading", "enterprises", "agency", "agencies", "corporation", "sales"]):
            return True, "MANDI_NON_FOOD_TRADER", "Generic trader located in agricultural grain market / mandi without food specialty"

    return False, "", ""


# ── The 6 Core Macro Outreach Categories ──────────────────────────────────────
MACRO_OUTREACH_CATEGORIES = {
    "distributor_wholesaler": {
        "code": "distributor_wholesaler",
        "name": "Distributors & Wholesalers",
        "pitch_focus": "Trade margins (35-42%), uniform MRP protection, PAN India logistics, fast shelf velocity",
        "target_skus": ["bold", "purista"],
    },
    "retail_chains": {
        "code": "retail_chains",
        "name": "Retail Chains & Supermarkets",
        "pitch_focus": "Retail margins (25-30%), display-ready glass packaging, 100% pure coffee differentiator against chicory blends",
        "target_skus": ["bold", "purica", "purista"],
    },
    "corporates": {
        "code": "corporates",
        "name": "Corporate Offices & Enterprise Pantries",
        "pitch_focus": "30-40% cost savings vs retail, high employee energy (zero chicory crash), direct subscription replenishment",
        "target_skus": ["bold"],
    },
    "gifting_companies": {
        "code": "gifting_companies",
        "name": "Corporate Gifting & Hamper Curators",
        "pitch_focus": "Aesthetic food-grade glass jars, custom hamper packaging, clean-label 100% pure coffee story",
        "target_skus": ["purica", "bold"],
    },
    "coffee_ingredient_manufacturers": {
        "code": "coffee_ingredient_manufacturers",
        "name": "Ingredient Manufacturers (Bakeries, Breweries, Confectionery, Cosmetics)",
        "pitch_focus": "100% Pure Coffee (zero chicory filler ensures unadulterated flavor/aroma), rapid 30s solubility, consistent roast profiles, bulk pricing",
        "target_skus": ["bold", "purista"],
    },
    "hospitality_institutional": {
        "code": "hospitality_institutional",
        "name": "HoReCa & Anywhere Else Coffee is Used (Hotels, Cafes, Restaurants, Institutions)",
        "pitch_focus": "Cost per cup economics (₹4.20/cup vs counter serving at ₹20-50 = 78% margin), complimentary sample kits, reliable fresh batch supply",
        "target_skus": ["bold", "purista"],
    },
}


_INGREDIENT_RE = re.compile(
    r"\b(bakery|bakers|bakehouse|patisserie|pastry|cake|cakes|confectionery|chocolatier|chocolate|chocolates|"
    r"ice\s*cream|gelato|brewery|brewing|brewpub|craft\s*beer|distillery|distilleries|flavor|flavour|flavours|"
    r"food\s*processing|cosmetics?|skincare|soap|soaps|caffeine\s*extract)\b",
    re.I
)

_GIFTING_RE = re.compile(
    r"\b(gifting|gift|gifts|hampers|hamper|corporate\s*gifts?|pr\s*agency|favors?|souvenir)\b",
    re.I
)

_RETAIL_CHAIN_RE = re.compile(
    r"\b(supermarket|supermarkets|hypermarket|hypermarkets|grocery\s*chain|department(?:al)?\s*store|departmental|modern\s*trade|mart|retail\s*chain|kirana)\b",
    re.I
)

_DISTRIBUTOR_RE = re.compile(
    r"\b(distributor|distributors|distribution|wholesaler|wholesalers|wholesale|fmcg\s*distributor|sub\s*stockist|stockist|c&f)\b",
    re.I
)

_CORPORATE_RE = re.compile(
    r"\b(corporate|corporates|office|offices|it\s*park|tech\s*park|infotech|solutions|technologies|software|systems|workplace|coworking|co-working|facility\s*management|enterprises?)\b",
    re.I
)


def classify_macro_outreach_segment(
    division: Any = None,
    company: str | None = None,
    description: str | None = None,
    segment: str | None = None,
    industry: str | None = None,
) -> tuple[str, str]:
    """
    Classifies a lead into one of the 6 core outreach pillars:
      1. distributor_wholesaler
      2. retail_chains
      3. corporates
      4. gifting_companies
      5. coffee_ingredient_manufacturers
      6. hospitality_institutional (anywhere else coffee is used)

    Returns:
        (category_code, category_display_name)
    """
    if hasattr(division, "company") or hasattr(division, "division") or hasattr(division, "category"):
        lead = division
        division = getattr(lead, "division", "")
        company = getattr(lead, "company", "")
        description = getattr(lead, "description", "") or getattr(lead, "notes", "") or getattr(lead, "qualification_notes", "")
        segment = getattr(lead, "segment", "") or getattr(lead, "category", "")
        industry = getattr(lead, "industry", "")

    blob = f"{division or ''} {company or ''} {description or ''} {segment or ''} {industry or ''}".strip().lower()

    # Pillar 5: Companies making other products using coffee as an ingredient
    if _INGREDIENT_RE.search(blob):
        return "coffee_ingredient_manufacturers", "Ingredient Manufacturers (Bakeries, Breweries, Confectionery, Cosmetics)"

    # Pillar 4: Gifting Companies
    if _GIFTING_RE.search(blob) or (segment or "").lower() == "corporate_gifting":
        return "gifting_companies", "Corporate Gifting & Hamper Curators"

    # Pillar 1: Distributor / Wholesaler
    if _DISTRIBUTOR_RE.search(blob) or (segment or "").lower() in ("distributor", "wholesaler"):
        return "distributor_wholesaler", "Distributors & Wholesalers"

    # Pillar 2: Retail Chains & Supermarkets
    if _RETAIL_CHAIN_RE.search(blob) or (segment or "").lower() in ("retail", "grocery", "modern_trade"):
        return "retail_chains", "Retail Chains & Supermarkets"

    # Pillar 3: Corporates & Offices
    if _CORPORATE_RE.search(blob) or (segment or "").lower() in ("corporate", "corporate_office", "office_pantry"):
        return "corporates", "Corporate Offices & Enterprise Pantries"

    # Pillar 6: Anywhere else coffee is used (HoReCa, Hotels, Cafes, Restaurants, Catering, Institutions)
    return "hospitality_institutional", "HoReCa & Anywhere Else Coffee is Used (Hotels, Cafes, Restaurants, Institutions)"

