"""Multi-source POI discovery for Purity Revenue OS.

Overture Places is the primary open POI source and OpenStreetMap/Overpass is a
secondary corroboration source. Neither provider grants contact consent or
changes outreach state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any

import httpx


@dataclass(frozen=True)
class DiscoveredBusiness:
    name: str
    latitude: float
    longitude: float
    category: str | None = None
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    email: str | None = None
    source: str = "unknown"
    source_id: str | None = None
    confidence: float | None = None
    source_record: dict[str, Any] = field(default_factory=dict)


_PHONE_RE = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")


def normalize_phone(value: str | None) -> str | None:
    """Tidy a number for STORAGE. Keeps it dialable.

    The country code stays. "+91 98765 43210" becomes "+919876543210", not
    "9876543210" — dropping +91 leaves a number nobody can dial from outside
    India, and downstream is_landline() and the WhatsApp adapter both expect
    the full form.
    """
    if not value:
        return None
    value = _PHONE_RE.sub("", str(value)).strip()
    return value or None


def phone_key(value: str | None) -> str | None:
    """Reduce a number to the digits that IDENTIFY the subscriber. Matching only.

    Storage and matching are different jobs and were previously done by one
    function. The same shop arrives from Overture as "+91-98765-43210" and from
    OSM as "098765 43210"; as stored values those are both correct and both
    different, so using the stored form as an identity key meant phone
    corroboration never fired — the entire purpose of merging two sources.

    The original tests passed anyway because their fixtures shared
    byte-identical coordinates, so the geo key matched instead and hid it.

    Last 10 digits, matching contact_enricher.digits_only, whatsapp_connector
    and import_call_sheet. One definition of "the same number" across the
    codebase, or this bug reappears somewhere else.
    """
    if not value:
        return None
    digits = _NON_DIGIT.sub("", str(value))
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if value is None:
        return None
    return str(value)


def overture_record_to_business(record: dict[str, Any]) -> DiscoveredBusiness | None:
    geometry = record.get("geometry") or {}
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    names = record.get("names") or {}
    addresses = record.get("addresses") or []
    address = addresses[0] if addresses else {}
    taxonomy = record.get("taxonomy") or {}
    return DiscoveredBusiness(
        name=str(names.get("primary") or "").strip(),
        latitude=float(coords[1]), longitude=float(coords[0]),
        category=record.get("basic_category") or taxonomy.get("primary"),
        address=address.get("freeform"), phone=normalize_phone(_first(record.get("phones"))),
        website=_first(record.get("websites")), email=_first(record.get("emails")),
        source="overture", source_id=str(record.get("id")) if record.get("id") else None,
        confidence=float(record["confidence"]) if record.get("confidence") is not None else None,
        source_record=record,
    )


def discover_overture(
    bbox: tuple[float, float, float, float],
    *,
    min_confidence: float = 0.0,
    categories: set[str] | None = None,
) -> list[DiscoveredBusiness]:
    """Query the latest Overture Places release inside bbox.

    bbox is west, south, east, north. The official overturemaps package reads
    only the required cloud-hosted GeoParquet ranges. It is imported lazily so
    Purity can boot when POI discovery is disabled or the optional package is
    unavailable.
    """
    try:
        from overturemaps import record_batch_reader
    except ImportError as exc:
        raise RuntimeError("Overture discovery is unavailable: install overturemaps") from exc
    reader = record_batch_reader("place", bbox=bbox)
    if reader is None:
        return []
    results: list[DiscoveredBusiness] = []
    for batch in reader:
        for row in batch.to_pylist():
            business = overture_record_to_business(row)
            if business is None or not business.name:
                continue
            if business.confidence is not None and business.confidence < min_confidence:
                continue
            if categories and business.category not in categories:
                continue
            results.append(business)
    return results


def osm_record_to_business(element: dict[str, Any]) -> DiscoveredBusiness | None:
    tags = element.get("tags") or {}
    lat, lon = element.get("lat"), element.get("lon")
    if lat is None or lon is None:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    name = (tags.get("name") or "").strip()
    if not name or lat is None or lon is None:
        return None
    address_parts = [tags.get(k) for k in ("addr:housenumber", "addr:street", "addr:city", "addr:postcode") if tags.get(k)]
    return DiscoveredBusiness(
        name=name, latitude=float(lat), longitude=float(lon),
        category=tags.get("amenity") or tags.get("shop") or tags.get("office"),
        address=", ".join(address_parts) or None,
        phone=normalize_phone(tags.get("phone") or tags.get("contact:phone")),
        website=tags.get("website") or tags.get("contact:website"),
        email=tags.get("email") or tags.get("contact:email"), source="osm",
        source_id=f"{element.get('type','element')}/{element.get('id')}" if element.get("id") else None,
        source_record=element,
    )


def build_overpass_query(
    bbox: tuple[float, float, float, float],
    amenities: tuple[str, ...] = ("cafe", "restaurant", "bakery"),
) -> str:
    """Build a bounded Overpass query using south,west,north,east."""
    south, west, north, east = bbox
    selectors = "".join(f'node["amenity"="{a}"]({south},{west},{north},{east});' for a in amenities)
    return f"[out:json][timeout:30];({selectors});out body center;"


async def discover_osm(
    bbox: tuple[float, float, float, float],
    *,
    endpoint: str | None = None,
    timeout: float = 45.0,
) -> list[DiscoveredBusiness]:
    endpoint = endpoint or os.getenv("OSM_OVERPASS_URL")
    if not endpoint:
        raise RuntimeError("OSM discovery is disabled: set OSM_OVERPASS_URL to a managed/self-hosted Overpass endpoint")
    headers = {"User-Agent": os.getenv("PURITY_POI_USER_AGENT", "PurityRevenueOS/1.0")}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post(endpoint, data={"data": build_overpass_query(bbox)})
        response.raise_for_status()
        payload = response.json()
    return [business for element in payload.get("elements", []) if (business := osm_record_to_business(element)) is not None]


def dedupe_key(business: DiscoveredBusiness) -> str:
    if business.source == "overture" and business.source_id:
        return f"gers:{business.source_id}"
    phone = phone_key(business.phone)
    if phone:
        return f"phone:{phone}"
    website = (business.website or "").lower().rstrip("/")
    if website:
        return f"web:{website}"
    return f"geo:{round(business.latitude,5)}:{round(business.longitude,5)}:{business.name.casefold()}"


def _identity_keys(business: DiscoveredBusiness) -> tuple[str, ...]:
    """Build corroborating identity keys without treating provider IDs as global."""
    keys: list[str] = []
    phone = phone_key(business.phone)
    if phone:
        keys.append(f"phone:{phone}")
    website = (business.website or "").strip().lower().rstrip("/")
    if website:
        website = re.sub(r"^https?://", "", website).removeprefix("www.")
        keys.append(f"web:{website}")
    if business.source == "overture" and business.source_id:
        keys.append(f"gers:{business.source_id}")
    keys.append(_geo_key(business, 0, 0))
    return tuple(keys)


# ~11 m at this latitude. Coarse enough that two providers describing one
# shopfront land in the same cell or an adjacent one; tight enough that
# distinct businesses do not collide.
_GEO_CELL = 1e-4


def _norm_name(business: DiscoveredBusiness) -> str:
    return " ".join(business.name.casefold().split())


def _geo_key(business: DiscoveredBusiness, di: int, dj: int) -> str:
    i = int(business.latitude // _GEO_CELL) + di
    j = int(business.longitude // _GEO_CELL) + dj
    return f"geo:{i}:{j}:{_norm_name(business)}"


def _lookup_keys(business: DiscoveredBusiness) -> tuple[str, ...]:
    """Keys to SEARCH by — the record's own cell plus its eight neighbours.

    Rounding coordinates to a grid has a boundary problem that no amount of
    precision fixes: 30.73330 and 30.73337 are 7.8 metres apart and fall either
    side of a cell edge, so they never match however fine or coarse the grid
    is. That made geo+name effectively dead as a corroborator — it fired only
    when two providers published byte-identical coordinates, which is also
    exactly why the original tests passed while phone matching was broken.

    Checking the neighbouring cells removes the boundary entirely: any two
    points within one cell of each other match, wherever the edges happen to
    fall. Registration still uses only the record's own cell, so this widens
    what can be found, not what a record claims to be.

    Geo+name stays the last resort, after phone and website. Over-merging is
    the dangerous direction — two branches of a chain collapsing into one
    record loses a real prospect and cannot be undone downstream, whereas a
    missed merge only leaves a duplicate that a later phone or website match
    will join.
    """
    keys = [k for k in _identity_keys(business) if not k.startswith("geo:")]
    keys += [_geo_key(business, di, dj)
             for di in (-1, 0, 1) for dj in (-1, 0, 1)]
    return tuple(keys)


def _merge_business(existing: DiscoveredBusiness, record: DiscoveredBusiness) -> DiscoveredBusiness:
    scores = [x for x in (existing.confidence, record.confidence) if x is not None]
    return DiscoveredBusiness(
        name=existing.name or record.name, latitude=existing.latitude, longitude=existing.longitude,
        category=existing.category or record.category, address=existing.address or record.address,
        phone=existing.phone or record.phone, website=existing.website or record.website,
        email=existing.email or record.email, source=f"{existing.source}+{record.source}",
        source_id=existing.source_id or record.source_id, confidence=max(scores) if scores else None,
        source_record={"primary": existing.source_record, "secondary": record.source_record},
    )


def merge_sources(records: list[DiscoveredBusiness]) -> list[DiscoveredBusiness]:
    """Merge corroborating records across providers; never infer consent or outreach state."""
    merged: list[DiscoveredBusiness] = []
    key_to_index: dict[str, int] = {}
    for record in records:
        # Search by lookup keys (own cell + neighbours); register only the
        # record's own keys, so widening the search never widens a claim.
        match_index = next((key_to_index[key] for key in _lookup_keys(record) if key in key_to_index), None)
        if match_index is None:
            match_index = len(merged)
            merged.append(record)
        else:
            merged[match_index] = _merge_business(merged[match_index], record)
        for key in _identity_keys(merged[match_index]):
            key_to_index[key] = match_index
    return merged
