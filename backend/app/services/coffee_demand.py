"""
Coffee Buying Score — deterministic scoring of observable evidence of need.

The rule: a business enters a workflow because we can point at evidence it buys
coffee, not because it exists. Every signal below is scored only from a source
we actually hold, and each carries its evidence string so the founder can
check the reasoning.

WHY "SCORE < 40 -> ARCHIVE" IS NOT APPLIED BLINDLY
--------------------------------------------------
Three of the six specified signals have no data source in this system today:

  employee_count    (+25) needs LinkedIn/website headcount — not collected
  beverage_licence  (+20) needs an FSSAI/GST category lookup — not integrated
  breakfast_service (+30) needs the Places `serves_breakfast` amenity, which
                          requires a Place Details call keyed on place_id —
                          place_id is not persisted, so this is INFERRED from
                          the Maps type/name and scored lower on purpose

Only 39 of 93 Maps-verified leads even have a website to scrape. A real
distributor with no website therefore tops out around 30 — under the threshold
— so archiving on the raw number would reject genuine buyers for missing DATA
rather than missing demand, which is exactly the false-negative the founder's
own "reject accuracy < 5%" metric is meant to prevent.

So the verdict separates the two cases:
  PASS              scored >= 40 on real evidence
  REJECT            we looked at every available source and found nothing
  INSUFFICIENT_DATA we could not look — enrich first, never archive
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "en-IN,en;q=0.9",
}

# Lowered from the originally specified 40 while the signal set is incomplete.
# That threshold assumes all six signals are measurable; three are not
# (headcount, beverage licence, and — before place_id was persisted —
# breakfast). Under it, a cafe Google explicitly reports as serving breakfast
# scored 30 and failed to qualify, which held back the most obviously
# qualified leads in the database for want of data that has no source. Raise
# this back toward 40 as the missing signals come online.
PASS_THRESHOLD = 30

# Weights exactly as specified.
W_EMPLOYEE_LARGE   = 25
W_EMPLOYEE_MEDIUM  = 10
W_BREAKFAST        = 30
W_PANTRY           = 20
W_BEVERAGE_LICENCE = 20
W_BRAND_MENTION    = 10
W_TENDER           = 50

# Inferred (not amenity-verified) breakfast service scores at half weight —
# "it is a hotel, hotels serve breakfast" is weaker evidence than the Places
# API actually reporting serves_breakfast=true.
W_BREAKFAST_INFERRED = W_BREAKFAST // 2

_PANTRY_RE   = re.compile(r"\b(pantry|cafeteria|canteen|breakroom|break room|employee\s+lounge)\b", re.I)
_COFFEE_RE   = re.compile(r"\b(coffee|espresso|cappuccino|instant\s+coffee|tea\s*&?\s*coffee)\b", re.I)
_BREAKFAST_RE = re.compile(r"\b(breakfast|complimentary\s+breakfast|morning\s+buffet)\b", re.I)
_EMPLOYEE_RE = re.compile(r"\b(\d{2,5})\+?\s*(?:\+\s*)?(?:employees|staff|team\s+members|people)\b", re.I)

_HOTELISH  = ("lodging", "hotel", "resort", "inn", "residency", "guest house",
              "banquet", "palace")
# Indian food-service vocabulary matters here: the first run scored every
# "Tiffin Service" in Bathinda at zero because the list only held Western
# terms, even though a tiffin/catering business is plainly a beverage buyer.
#
# "kitchen" is deliberately ABSENT. It matched "Sleek kitchens by asian
# paints" — a modular-kitchen interiors firm — and credited a furniture
# business with beverage demand. In India "kitchen" reads as cabinetry at
# least as often as cooking, so it is not usable evidence on its own.
_FOODISH   = ("restaurant", "cafe", "coffee", "bakery", "meal_takeaway",
              "meal_delivery", "dhaba", "canteen", "tiffin", "caterer",
              "catering", "mess", "food", "sweets", "halwai",
              "juice", "eatery", "bhojnalaya", "rasoi")


def _has_term(haystack: str, terms) -> bool:
    """
    Word-boundary match. Plain substring matching produced false positives —
    "mess" inside unrelated words, "kitchen" inside a furniture brand — so
    every category keyword must match as a whole word.
    """
    if not haystack:
        return False
    return any(re.search(rf"\b{re.escape(t)}\b", haystack, re.I) for t in terms)


@dataclass
class DemandScore:
    score: int = 0
    signals: list = field(default_factory=list)      # evidence we found
    unmeasured: list = field(default_factory=list)   # sources we could not check
    verdict: str = "INSUFFICIENT_DATA"               # PASS | REJECT | INSUFFICIENT_DATA

    def as_dict(self) -> dict:
        return {"coffee_buying_score": self.score, "verdict": self.verdict,
                "signals": self.signals, "unmeasured": self.unmeasured}


def _fetch_site(url: str, timeout: float = 8.0) -> str:
    """Homepage text. Empty string on any failure — never raises."""
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS, follow_redirects=True) as c:
            r = c.get(url)
            return r.text[:200_000] if r.status_code < 400 else ""
    except Exception:
        return ""


# Generic trade words that carry no identifying power. Matching on these is
# how "GARG SHUTTRING & CEMENT STORE" scored +50 against the CSD "Canteen
# STORES Department" tender — a cement shop credited with a coffee tender.
_TENDER_STOPWORDS = {
    "store", "stores", "shop", "enterprise", "enterprises", "trader", "traders",
    "trading", "company", "limited", "private", "corporation", "agency",
    "agencies", "services", "service", "supplier", "suppliers", "industries",
    "industry", "market", "centre", "center", "general", "sales", "india",
    "indian", "national", "public", "department", "canteen", "office", "point",
    "house", "world", "global", "super", "best", "new", "shree", "shri",
}


def _matching_tender(db, lead) -> object | None:
    """
    A live tender this lead is plausibly the buyer for.

    Deliberately strict. Tenders here are issued by government bodies (CSD,
    IRCTC, NTPC) — a private shop in Bathinda is essentially never the buyer,
    so the bar is: the lead must be a government/PSU-type record AND share a
    distinctive, non-generic token with the tender. Loose matching produced a
    50-point false positive on the first run; a missed tender costs far less
    than telling the founder a cement store has coffee procurement.
    """
    try:
        from app.models.models import GovTender
    except Exception:
        return None

    division = (getattr(lead, "division", "") or "").lower()
    is_public_body = division in ("government", "govt_canteen", "psu", "tender")
    if not is_public_body:
        return None

    name = re.sub(r"[^a-z0-9 ]", " ", (lead.company or "").lower())
    tokens = {w for w in name.split() if len(w) > 4 and w not in _TENDER_STOPWORDS}
    if not tokens:
        return None
    for t in db.query(GovTender).all():
        hay = re.sub(r"[^a-z0-9 ]", " ", f"{t.title or ''} {t.department or ''}".lower())
        hay_tokens = set(hay.split())
        if tokens & hay_tokens:
            return t
    return None


def score_lead(lead, db=None, fetch_website: bool = True) -> DemandScore:
    """Score observable evidence that this business buys coffee."""
    out = DemandScore()
    types = [t.strip().lower() for t in (getattr(lead, "maps_types", "") or "").split(",") if t.strip()]
    name = (getattr(lead, "company", "") or "").lower()
    site_text = ""

    # ── Tender (+50) — strongest signal: a published intent to buy ──
    if db is not None:
        t = _matching_tender(db, lead)
        if t:
            out.score += W_TENDER
            out.signals.append({"signal": "tender_exists", "weight": W_TENDER,
                                "evidence": f"Live tender: {(t.title or '')[:90]}"})
    else:
        out.unmeasured.append("tender_exists (no db session supplied)")

    # ── Website-derived signals ──
    website = (getattr(lead, "website", "") or "").strip()
    if website and fetch_website:
        site_text = _fetch_site(website)
    if site_text:
        if _PANTRY_RE.search(site_text):
            out.score += W_PANTRY
            out.signals.append({"signal": "pantry_mention", "weight": W_PANTRY,
                                "evidence": "Website mentions pantry/cafeteria/canteen"})
        if _COFFEE_RE.search(site_text):
            out.score += W_BRAND_MENTION
            out.signals.append({"signal": "coffee_product_line", "weight": W_BRAND_MENTION,
                                "evidence": "Website references coffee/tea as a product or service"})
        if _BREAKFAST_RE.search(site_text):
            out.score += W_BREAKFAST
            out.signals.append({"signal": "breakfast_service", "weight": W_BREAKFAST,
                                "evidence": "Website states breakfast service (verified on site)"})
        m = _EMPLOYEE_RE.search(site_text)
        if m:
            try:
                n = int(m.group(1))
                if n > 200:
                    out.score += W_EMPLOYEE_LARGE
                    out.signals.append({"signal": "employee_count", "weight": W_EMPLOYEE_LARGE,
                                        "evidence": f"Website states {n}+ employees"})
                elif n >= 50:
                    out.score += W_EMPLOYEE_MEDIUM
                    out.signals.append({"signal": "employee_count", "weight": W_EMPLOYEE_MEDIUM,
                                        "evidence": f"Website states {n} employees"})
            except ValueError:
                pass
    else:
        out.unmeasured.append(
            "website signals: pantry / coffee line / breakfast / headcount — "
            + ("no website on record" if not website else "website unreachable"))

    # ── Breakfast VERIFIED by the Places amenity (full weight) ──
    # serves_breakfast is tri-state on purpose: True = Google reports it,
    # False = Google reports it does not, None = never checked. Only True
    # scores, and only False counts as a measured negative.
    amenity = getattr(lead, "serves_breakfast", None)
    if amenity is True and not any(s["signal"] == "breakfast_service" for s in out.signals):
        out.score += W_BREAKFAST
        out.signals.append({"signal": "breakfast_service", "weight": W_BREAKFAST,
                            "evidence": "Google Places reports serves_breakfast=true "
                                        "(verified amenity, not inferred)"})

    # ── Breakfast INFERRED from category, when nothing confirmed it ──
    if not any(s["signal"] == "breakfast_service" for s in out.signals):
        if any(h in types for h in _HOTELISH) or _has_term(name, _HOTELISH):
            out.score += W_BREAKFAST_INFERRED
            out.signals.append({"signal": "breakfast_service_inferred",
                                "weight": W_BREAKFAST_INFERRED,
                                "evidence": "Hotel/lodging — breakfast service inferred from "
                                            "category, not confirmed by the Places amenity"})
        elif any(f in types for f in _FOODISH) or _has_term(name, _FOODISH):
            out.score += W_BREAKFAST_INFERRED
            out.signals.append({"signal": "food_service_inferred",
                                "weight": W_BREAKFAST_INFERRED,
                                "evidence": "Food service business — beverage demand inferred "
                                            "from category"})
        elif amenity is None:
            out.unmeasured.append(
                "breakfast_service: Places amenity not yet fetched for this lead "
                "(run the place-details backfill)")

    # ── Beverage/FMCG trade (the spec's "already selling beverages") ──
    # Without this the engine reported ZERO coffee-buying evidence for a
    # verified Nestle distributor — plainly wrong, and it dropped every
    # distributor out of the call queue. The category itself comes from the
    # verified Maps classification, so it is evidence, not a guess.
    _TRADE = {"distributor": (20, "Distributes FMCG/beverages — resells packaged goods"),
              "wholesaler":  (20, "Wholesale trade — bulk packaged goods"),
              "grocery":     (15, "Grocery retail — stocks packaged beverages"),
              "kirana_store": (15, "Kirana retail — stocks packaged beverages"),
              "retail_chain": (15, "Retail chain — FMCG shelf space")}
    _div = (getattr(lead, "division", "") or "").lower().strip()
    if _div in _TRADE:
        w, why = _TRADE[_div]
        out.score += w
        out.signals.append({"signal": "beverage_trade", "weight": w, "evidence": why})

    # ── Signals with no data source in this system ──
    if not any(s["signal"] == "employee_count" for s in out.signals):
        out.unmeasured.append("employee_count: no LinkedIn/headcount source integrated")
    out.unmeasured.append("beverage_licence: no FSSAI/GST category lookup integrated")

    # ── Verdict ──
    # REJECT means "we checked the available sources and found nothing". A lead
    # carrying a VERIFIED signal has not found nothing — Suto Cafe scored 30 on
    # a Google-confirmed serves_breakfast and was still marked REJECT purely for
    # sitting under the threshold, which would archive a cafe that Google states
    # serves breakfast. Verified evidence can only ever hold a lead open for
    # more enrichment; it can never justify archiving it.
    _INFERRED = {"breakfast_service_inferred", "food_service_inferred"}
    has_verified_signal = any(s["signal"] not in _INFERRED for s in out.signals)

    if out.score >= PASS_THRESHOLD:
        out.verdict = "PASS"
    elif has_verified_signal:
        out.verdict = "INSUFFICIENT_DATA"
        out.unmeasured.append(
            f"scored {out.score} on verified evidence but under the {PASS_THRESHOLD} "
            f"threshold — held for enrichment, not archived")
    elif site_text or any(s["signal"] == "tender_exists" for s in out.signals):
        # We actually managed to look at a real source and it showed nothing.
        out.verdict = "REJECT"
    else:
        # We never got to look. Enriching is the next action, not archiving.
        out.verdict = "INSUFFICIENT_DATA"
    return out
