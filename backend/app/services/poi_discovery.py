"""Multi-source POI discovery for Purity Revenue OS.

The discovery layer is deliberately provider-neutral. Overture Places is the
primary open POI source and OpenStreetMap/Overpass is a secondary corroboration
source. Neither provider grants contact consent or changes outreach state.
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


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    value = _PHONE_RE.sub("", value).strip()
    return value or None


def _first(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return str(value[0])
    if value is None:
        return None
    return str(value)


def overture_record_to_business(record: dict[str, Any]) -> DiscoveredBusiness | None:
    """Normalize an Overture Places record without making any sales decision."""
    geometry = record.get("geometry") or {}
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None

    names = record.get("names") or {}
    address = (record.get("addresses") or [{}])[0] or {}
    taxonomy = record.get("taxonomy") or {}
    return DiscoveredBusiness(
        name=str(names.get("primary") or "").strip(),
        latitude=float(coords[1]),
        longitude=float(coords[0]),
        category=record.get("basic_category") or taxonomy.get("primary"),
        address=address.get("freeform"),
        phone=normalize_phone(_first(record.get("phones"))),
        website=_first(record.get("websites")),
        email=_first(record.get("emails")),
        source="overture",
        source_id=str(record.get("id")) if record.get("id") else None,
        confidence=float(record["confidence"]) if record.get("confidence") is not None else None,
        source_record=record,
    )


def osm_record_to_business(element: dict[str, Any]) -> DiscoveredBusiness | None:
    """Normalize an OSM element returned by Overpass."""
    tags = element.get("tags") or {}
    lat = element.get("lat")
    lon = element.get("lon")
    if lat is None or lon is None:
        center = element.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
    name = (tags.get("name") or "").strip()
    if not name or lat is None or lon is None:
        return None

    address_parts = [tags.get(k) for k in ("addr:housenumber", "addr:street", "addr:city", "addr:postcode") if tags.get(k)]
    return DiscoveredBusiness(
        name=name,
        latitude=float(lat),
        longitude=float(lon),
        category=tags.get("amenity") or tags.get("shop") or tags.get("office"),
        address=", ".join(address_parts) or None,
        phone=normalize_phone(tags.get("phone") or tags.get("contact:phone")),
        website=tags.get("website") or tags.get("contact:website"),
        email=tags.get("email") or tags.get("contact:email"),
        source="osm",
        source_id=f"{element.get('type','element')}/{element.get('id')}" if element.get("id") else None,
        confidence=None,
        source_record=element,
    )


def build_overpass_query(bbox: tuple[float, float, float, float], amenities: tuple[str, ...] = ("cafe", "restaurant", "bakery")) -> str:
    """Build a bounded query: south,west,north,east.

    The endpoint is configurable because regular commercial use should not
    depend on a public shared Overpass instance. Keep the default unset.
    """
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

    query = build_overpass_query(bbox)
    headers = {"User-Agent": os.getenv("PURITY_POI_USER_AGENT", "PurityRevenueOS/1.0")}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post(endpoint, data={"data": query})
        response.raise_for_status()
        payload = response.json()

    return [
        business
        for element in payload.get("elements", [])
        if (business := osm_record_to_business(element)) is not None
    ]


def dedupe_key(business: DiscoveredBusiness) -> str:
    """Conservative deterministic key used before fuzzy entity resolution."""
    if business.source == "overture" and business.source_id:
        return f"gers:{business.source_id}"
    phone = normalize_phone(business.phone)
    if phone:
        return f"phone:{phone}"
    website = (business.website or "").lower().rstrip("/")
    if website:
        return f"web:{website}"
    return f"geo:{round(business.latitude,5)}:{round(business.longitude,5)}:{business.name.casefold()}"


def merge_sources(records: list[DiscoveredBusiness]) -> list[DiscoveredBusiness]:
    """Conservatively merge exact keys; never infer consent or outreach state."""
    merged: dict[str, DiscoveredBusiness] = {}
    for record in records:
        key = dedupe_key(record)
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
            continue
        # Prefer the richer record while preserving the original source data.
        merged[key] = DiscoveredBusiness(
            name=existing.name or record.name,
            latitude=existing.latitude,
            longitude=existing.longitude,
            category=existing.category or record.category,
            address=existing.address or record.address,
            phone=existing.phone or record.phone,
            website=existing.website or record.website,
            email=existing.email or record.email,
            source=f"{existing.source}+{record.source}",
            source_id=existing.source_id or record.source_id,
            confidence=max(x for x in (existing.confidence, record.confidence) if x is not None) if (existing.confidence is not None or record.confidence is not None) else None,
            source_record={"primary": existing.source_record, "secondary": record.source_record},
        )
    return list(merged.values())
