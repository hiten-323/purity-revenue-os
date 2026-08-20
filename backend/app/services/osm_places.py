"""
OpenStreetMap places discovery — the ONLY maps provider for lead discovery.

Nominatim + Overpass. No Google Places / Geocoding calls.

Honest limits:
  - ratings / review counts always NULL
  - phone/website only when tagged in OSM
  - coverage uneven in India; respect Nominatim ~1 req/s
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

import httpx

_UA = os.getenv(
    "OSM_USER_AGENT",
    "PurityRevenueOS/1.0 (B2B lead discovery; contact=connect@purepantryprovisions.com)",
)
_NOMINATIM = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
_OVERPASS = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")

_last_nominatim = 0.0

_QUERY_TAGS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"cafe|coffee|restaurant|hotel|cater", re.I),
     ['node["amenity"~"^(cafe|restaurant|hotel|fast_food)$"]',
      'way["amenity"~"^(cafe|restaurant|hotel|fast_food)$"]',
      'node["tourism"="hotel"]', 'way["tourism"="hotel"]']),
    (re.compile(r"grocery|kirana|supermarket|retail|mart", re.I),
     ['node["shop"~"^(supermarket|convenience|grocery|general)$"]',
      'way["shop"~"^(supermarket|convenience|grocery|general)$"]']),
    (re.compile(r"distributor|wholesale|warehouse|fmcg|beverage", re.I),
     ['node["shop"~"^(wholesale|yes)$"]',
      'node["office"="company"]',
      'node["industrial"]',
      'way["landuse"="industrial"]']),
    (re.compile(r"hospital|school|college|university|hostel", re.I),
     ['node["amenity"~"^(hospital|school|college|university)$"]',
      'way["amenity"~"^(hospital|school|college|university)$"]']),
    (re.compile(r"office|corporate|cowork|business park", re.I),
     ['node["office"]', 'way["office"]',
      'node["building"="commercial"]']),
]

_DEFAULT_TAGS = [
    'node["amenity"~"^(cafe|restaurant|hotel)$"]',
    'node["shop"~"^(supermarket|convenience|wholesale)$"]',
    'node["office"]',
]


def _throttle_nominatim() -> None:
    global _last_nominatim
    gap = 1.15 - (time.monotonic() - _last_nominatim)
    if gap > 0:
        time.sleep(gap)
    _last_nominatim = time.monotonic()


def _norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return phone or ""


def geocode_city(city: str, country: str = "India") -> Optional[tuple[float, float, dict]]:
    q = f"{city}, {country}".strip()
    _throttle_nominatim()
    try:
        with httpx.Client(timeout=25, headers={"User-Agent": _UA}) as client:
            r = client.get(
                f"{_NOMINATIM}/search",
                params={"q": q, "format": "json", "limit": 1, "addressdetails": 1},
            )
            r.raise_for_status()
            data = r.json() or []
            if not data:
                return None
            hit = data[0]
            lat = float(hit["lat"])
            lon = float(hit["lon"])
            bb = hit.get("boundingbox") or []
            if len(bb) == 4:
                south, north, west, east = map(float, bb)
            else:
                d = 0.08
                south, north, west, east = lat - d, lat + d, lon - d, lon + d
            return lat, lon, {"south": south, "north": north, "west": west, "east": east}
    except Exception as e:
        print(f"[OSM] Nominatim geocode failed for {city!r}: {e}")
        return None


def _tags_for_query(query: str) -> list[str]:
    for rx, tags in _QUERY_TAGS:
        if rx.search(query or ""):
            return tags
    return list(_DEFAULT_TAGS)


def _osm_types(tags: dict) -> list[str]:
    out = []
    for k in ("amenity", "shop", "office", "tourism", "industrial"):
        if tags.get(k):
            out.append(str(tags[k]).lower())
            out.append(k)
    return out


def search_osm_places(
    query: str,
    city: str,
    max_results: int = 20,
) -> list[dict]:
    geo = geocode_city(city)
    if not geo:
        return []
    _lat, _lon, bb = geo
    tag_filters = _tags_for_query(query)
    parts = []
    for tf in tag_filters:
        if tf.startswith("node") or tf.startswith("way"):
            parts.append(
                f"  {tf}({bb['south']},{bb['west']},{bb['north']},{bb['east']});"
            )
    body = (
        "[out:json][timeout:45];\n"
        "(\n" + "\n".join(parts) + "\n);\n"
        "out center tags 40;"
    )
    try:
        with httpx.Client(timeout=50, headers={"User-Agent": _UA}) as client:
            r = client.post(_OVERPASS, content=body.encode("utf-8"))
            r.raise_for_status()
            elements = (r.json() or {}).get("elements") or []
    except Exception as e:
        print(f"[OSM] Overpass failed for {city!r}/{query!r}: {e}")
        return []

    results: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or tags.get("name:en") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        if el.get("type") == "way" and "center" in el:
            lat = el["center"].get("lat")
            lon = el["center"].get("lon")
        else:
            lat = el.get("lat")
            lon = el.get("lon")

        phone = tags.get("phone") or tags.get("contact:phone") or ""
        website = tags.get("website") or tags.get("contact:website") or ""
        addr_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:suburb", ""),
            tags.get("addr:city", "") or city,
        ]
        address = ", ".join(p for p in addr_parts if p)
        osm_id = f"{el.get('type', 'node')}/{el.get('id', '')}"
        results.append({
            "company": re.sub(r"\s+", " ", name),
            "city": city,
            "lead_source": "OpenStreetMap",
            "phone": _norm_phone(phone),
            "address": address,
            "rating": None,
            "maps_rating": None,
            "maps_reviews_count": None,
            "types": _osm_types(tags),
            "place_id": f"osm:{osm_id}",
            "contact_name": "",
            "email": tags.get("email") or tags.get("contact:email") or "",
            "website": website,
            "latitude": lat,
            "longitude": lon,
        })
        if len(results) >= max_results:
            break
    return results


def discovery_maps_provider() -> str:
    """Always osm for discovery. Env kept for docs only."""
    return "osm"


def install_osm_maps_fallback() -> None:
    """
    Replace lead_discovery.search_google_maps with OSM-only search.
    Google is not called for discovery.
    """
    from app.services import lead_discovery as ld

    if getattr(ld, "_osm_fallback_installed", False):
        return

    def search_places(query, city, api_key=None, max_results=20):
        # api_key ignored — discovery does not use Google
        return search_osm_places(query, city, max_results=max_results)

    ld.search_google_maps = search_places  # type: ignore
    ld._osm_fallback_installed = True
    print("[OSM] discovery maps = OpenStreetMap only (Google removed from discovery)")
