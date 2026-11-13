"""
Where a lead sits relative to the business, and how much that should matter.

Geography decides where to LOOK. Fit, value, intent and history decide who to
CONTACT. So territory is a multiplier on commercial score, never a filter —
a high-fit Gurugram hotel should outrank a low-fit Abohar restaurant, and a
hard radius filter makes that impossible to express.

Deliberately not built on the `region` column: it reads "South" on 1,751 leads
that are all in Punjab, so it is a wrong default rather than evidence.
"""
from __future__ import annotations

import math

# Pure Pantry Provisions, Abohar — PIN 152116.
ORIGIN = (30.1445, 74.1954)
ORIGIN_CITY = "Abohar"

# Progressive rings. Local-first without being local-only.
RINGS = ((5, "R1_0_5"), (15, "R2_5_15"), (30, "R3_15_30"),
         (50, "R4_30_50"), (100, "R5_50_100"))

NCR_CITIES = {"delhi", "new delhi", "gurugram", "gurgaon", "noida",
              "greater noida", "ghaziabad", "faridabad"}

# Priority multipliers, applied to commercial score.
MULTIPLIER = {
    "R1_0_5": 1.25, "R2_5_15": 1.25, "R3_15_30": 1.15, "R4_30_50": 1.15,
    "R5_50_100": 1.10,
    "PUNJAB_BELT": 1.00,   # where 87% of the current base actually is
    "DELHI_NCR": 1.00,     # expansion market, equal weight by policy
    "OTHER_INDIA": 0.50,
    "UNKNOWN": 0.90,       # not geocoded: neither promoted nor buried
}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def distance_from_origin(lead) -> float | None:
    la, lo = getattr(lead, "latitude", None), getattr(lead, "longitude", None)
    if la in (None, 0) or lo in (None, 0):
        return None
    return round(haversine_km(ORIGIN, (float(la), float(lo))), 2)


def territory_of(lead) -> str:
    """
    Label from evidence, worst-assumption-last.

    NCR is matched on city, not radius: it is a named expansion market roughly
    350 km away, not "the next ring after Abohar", and treating it as a ring
    would bury it behind every Punjab town.
    """
    if (getattr(lead, "city", "") or "").strip().lower() in NCR_CITIES:
        return "DELHI_NCR"
    d = distance_from_origin(lead)
    if d is None:
        return "UNKNOWN"
    for limit, name in RINGS:
        if d <= limit:
            return name
    if (getattr(lead, "state", "") or "").strip().lower() == "punjab":
        return "PUNJAB_BELT"
    return "OTHER_INDIA"


def multiplier_for(lead) -> float:
    return MULTIPLIER.get(territory_of(lead), 1.0)


def priority(lead, commercial_score: float | None = None) -> float:
    """
    commercial_score x territory multiplier.

    Defaults to coffee_buying_score because that is the only fit signal with
    real, discriminating values today — intent_score is empty, estimated_value
    and final_score are the same constant on every lead.
    """
    base = commercial_score
    if base is None:
        base = getattr(lead, "coffee_buying_score", 0) or 0
    return round(float(base) * multiplier_for(lead), 2)
