"""
Send-queue quality gate: is this lead fit to be CONTACTED, not merely reachable?

The trust engine answers "can this address receive mail". This answers a
different question the pipeline never asked: is the record itself good enough
to put in front of a real business.

It exists because a preview of the next 20 sends contained a subject line
reading:

    A sample for WrkPod | Coworking Space in Coimbatore | Shared Office Space?

That is a scraped directory listing used verbatim as a customer-facing name.
Three leads also carried city="Bathinda" while their names said Mohali,
Coimbatore and Kankarbagh (Patna) — the discovery record was trusted blindly.

DESIGN RULE: the raw source value is never overwritten. display_name is
derived at render time, so provenance survives and re-learning stays possible.
"""
from __future__ import annotations

import re
from typing import Any

# Buckets. A candidate lands in exactly one, worst-first.
READY_TO_SEND     = "READY_TO_SEND"
SUPPRESSED        = "SUPPRESSED"
ALREADY_CONTACTED = "ALREADY_CONTACTED"
LOCATION_CONFLICT = "LOCATION_CONFLICT"
NAME_CLEANUP      = "NAME_CLEANUP"
NEEDS_ENRICHMENT  = "NEEDS_ENRICHMENT"
LOW_FIT           = "LOW_FIT"

# Below this, category evidence says coffee is not part of the business.
# 0 is NOT low fit — it means no evidence matched, which is NEEDS_ENRICHMENT.
LOW_FIT_BELOW = 40

# Marketing tails a directory appends. Cutting at the first one keeps the
# actual trading name and drops the SEO.
_TAIL = re.compile(
    r"\s*[\|\u2013\u2014]\s*.*$"                      # anything after | – —
    r"|\s+-\s+(?:shared|coworking|multispeciality|multi[- ]?speciality|best|top|no\.?\s*1)\b.*$"
    r"|\s*\(\s*(?:since|est\.?)\s*\d{4}\s*\)\s*$"       # "( SINCE 2008)" — note the space
    r"|\s*/\s*.*(?:services|solutions).*$",          # "X / X CATERING SERVICES"
    re.I)
_LOC_SUFFIX = re.compile(r"\s+(?:in|near|at)\s+[A-Z][\w\s]*$")
_LEGAL = re.compile(r"\s+(pvt\.?\s*ltd\.?|private limited|llp|inc\.?)\s*$", re.I)


def display_name(raw: str) -> str:
    """
    Customer-facing name derived from the raw record. Never stored over it.

    "WrkPod | Coworking Space in Coimbatore | Shared Office Space" -> "WrkPod"
    "BINNY CATERER'S & EVENT PLANNER( SINCE 2008)" -> "Binny Caterer's & Event Planner"
    """
    n = (raw or "").strip()
    n = _TAIL.sub("", n)
    n = _LOC_SUFFIX.sub("", n)
    n = _LEGAL.sub("", n)
    n = re.sub(r"\s{2,}", " ", n).strip(" -–—|,.")
    # ALLCAPS reads as shouting in a subject line; Title Case unless it is an
    # acronym short enough to be deliberate (BG, OASIS, IBM).
    if n.isupper() and len(n) > 6:
        n = _title(n)
    return n or (raw or "").strip()


def _title(n: str) -> str:
    """
    Title-case that does not mangle apostrophes.

    str.title() capitalises the letter after any non-alpha, so
    "CATERER'S" becomes "Caterer'S". A buyer reads that as a machine wrote it,
    which is exactly the impression cold outreach cannot afford.
    """
    out = []
    for word in n.split():
        if len(word) <= 3 and word.isalpha():      # BG, MK, SMS stay acronyms
            out.append(word)
            continue
        out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def name_needs_cleanup(raw: str) -> bool:
    """True when the raw name is unfit to show a buyer as-is."""
    n = (raw or "").strip()
    return bool(n) and (display_name(n) != n)


def location_conflict(raw_name: str, city: str) -> str | None:
    """
    A city named INSIDE the company name that contradicts the city field.

    Returns the conflicting city, or None. Discovery recorded several leads
    under the search city rather than their real one, so the record disagrees
    with itself and neither side has been verified.
    """
    n, c = (raw_name or ""), (city or "").strip().lower()
    if not c:
        return None
    m = re.search(r"\b(?:in|near|at)\s+([A-Z][a-zA-Z]{3,})", n)
    if not m:
        return None
    named = m.group(1).strip()
    return named if named.lower() != c else None


def classify_candidate(lead: Any, db: Any = None) -> dict:
    """
    One bucket per candidate, worst-first so the most serious reason wins.
    Never sends; returns a verdict for the preview and for the engine to act on.
    """
    raw = getattr(lead, "company", "") or ""
    city = getattr(lead, "city", "") or ""
    fit = getattr(lead, "coffee_buying_score", 0) or 0

    if getattr(lead, "do_not_call", False) or (getattr(lead, "status", "") or "") in (
            "DO_NOT_CONTACT", "CLOSED_LOST", "DISQUALIFIED"):
        return {"bucket": SUPPRESSED, "reason": "opted out or disqualified",
                "display_name": display_name(raw)}

    conflict = location_conflict(raw, city)
    if conflict:
        return {"bucket": LOCATION_CONFLICT,
                "reason": f'name says "{conflict}", record says "{city}" — unverified',
                "display_name": display_name(raw)}

    # 0 means no category evidence matched, which is a gap, not a verdict.
    if fit == 0:
        return {"bucket": NEEDS_ENRICHMENT,
                "reason": "no category evidence — fit unknown, classify before contacting",
                "display_name": display_name(raw)}

    if fit < LOW_FIT_BELOW:
        return {"bucket": LOW_FIT, "reason": f"fit {fit} — coffee not part of the business",
                "display_name": display_name(raw)}

    clean = display_name(raw)

    # NAME_CLEANUP only withholds when cleaning CANNOT produce a usable name.
    #
    # It used to block on "the stored name is messy", which was right while raw
    # values reached subject lines. Now every renderer derives the customer-
    # facing name through display_name, so a messy record no longer reaches a
    # buyer — and withholding 25 otherwise-good leads for a problem already
    # solved downstream is a gate punishing the wrong thing.
    if not clean or len(clean) < 3:
        return {"bucket": NAME_CLEANUP,
                "reason": f"cannot derive a usable display name from {raw[:40]!r}",
                "display_name": clean}

    return {"bucket": READY_TO_SEND,
            "reason": f"fit {fit}, identity clean"
                      + ("" if clean == raw.strip() else f" (shown as \"{clean}\")"),
            "display_name": clean,
            "name_cleaned": clean != raw.strip()}


def preview(db, leads: list) -> dict:
    """Bucketed send preview. Reports; sends nothing."""
    from collections import defaultdict
    out = defaultdict(list)
    for l in leads:
        v = classify_candidate(l, db)
        out[v["bucket"]].append({
            "lead_id": getattr(l, "id", None),
            "raw": getattr(l, "company", ""),
            "display_name": v["display_name"],
            "fit": getattr(l, "coffee_buying_score", 0) or 0,
            "reason": v["reason"],
        })
    order = [READY_TO_SEND, NAME_CLEANUP, LOCATION_CONFLICT,
             NEEDS_ENRICHMENT, LOW_FIT, ALREADY_CONTACTED, SUPPRESSED]
    return {"total": len(leads),
            "buckets": {b: out[b] for b in order if out[b]},
            "ready": len(out[READY_TO_SEND])}

# ── How a category is described TO A BUYER ───────────────────────────────────
# Internal taxonomy is not English. render_email interpolated the raw division
# into "We are speaking with selected {cat} businesses in {city}", which for
# 502 leads produced "selected retail_kirana businesses" and "selected
# corporate_office businesses" — underscores and all — while HORECA is trade
# jargon a Ludhiana hotelier would read as a typo.
#
# Plural nouns, so the sentence reads "selected hotels in Ludhiana" rather than
# the clumsy "selected hotel businesses in Ludhiana".
_AUDIENCE = {
    "cafe": "cafés",
    "restaurant": "restaurants",
    "hotel": "hotels",
    "horeca": "hotels and restaurants",
    "distributor": "distributors",
    "wholesaler": "wholesalers",
    "supermarket": "supermarkets",
    "modern_trade": "retail chains",
    "retail_kirana": "kirana and grocery stores",
    "retail": "retailers",
    "retailer": "retailers",
    "corporate_office": "offices",
    "corporate": "offices",
    "office_pantry": "office pantries",
    "facility_management": "facilities and pantry teams",
    "manufacturing": "manufacturers",
    "hospital": "hospitals",
    "school": "schools",
    "college": "colleges",
    "canteen_org": "canteens",
    "catering": "caterers",
    "gifting": "corporate gifting businesses",
    "procurement": "procurement teams",
}


def audience_label(category: str | None) -> str:
    """
    Buyer-facing plural for a lead category.

    Falls back to "businesses" rather than echoing an unknown internal key —
    a generic word is better than leaking taxonomy, and silence beats jargon.
    """
    key = (category or "").strip().lower().replace(" ", "_")
    return _AUDIENCE.get(key, "businesses")
