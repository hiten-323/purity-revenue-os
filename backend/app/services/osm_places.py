"""
OpenStreetMap places discovery — the ONLY maps provider for lead discovery.

Primary: Nominatim search (already proven live; ~1 req/s, required User-Agent).
Secondary: Overpass around: queries on several public mirrors (overpass-api.de 504s).

Honest limits:
  - ratings / review counts always NULL
  - phone/website only when tagged in OSM
  - coverage uneven in India
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
_OVERPASS_MIRRORS = [
    u.strip() for u in (
        os.getenv("OVERPASS_URL") or ""
    ).split(",") if u.strip()
] or [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

_last_nominatim = 0.0

# query fragment -> (nominatim amenity/shop keyword, overpass node filter)
_INTENT: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"cafe|coffee", re.I), "cafe", 'node["amenity"="cafe"]'),
    (re.compile(r"restaurant|dhaba|cater", re.I), "restaurant", 'node["amenity"="restaurant"]'),
    (re.compile(r"hotel|lodging|resort", re.I), "hotel", 'node["tourism"="hotel"]'),
    (re.compile(r"grocery|kirana|supermarket|retail|mart", re.I),
     "supermarket", 'node["shop"~"^(supermarket|convenience|grocery)$"]'),
    (re.compile(r"hospital", re.I), "hospital", 'node["amenity"="hospital"]'),
    (re.compile(r"school|college|university|hostel", re.I), "school", 'node["amenity"~"^(school|college|university)$"]'),
    (re.compile(r"office|corporate|cowork", re.I), "office", 'node["office"]'),
    (re.compile(r"distributor|wholesale|warehouse|fmcg", re.I),
     "wholesale", 'node["shop"="wholesale"]'),
]


def _intent(query: str) -> tuple[str, str]:
    for rx, amenity, overpass in _INTENT:
        if rx.search(query or ""):
            return amenity, overpass
    # free-text fallback: use the first word of the query as amenity-ish term
    word = re.split(r"\s+", (query or "cafe").strip())[0] or "cafe"
    return word, f'node["amenity"="{word}"]'


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


def _lead(name: str, city: str, *, lat, lon, address="", phone="",
          website="", email="", types=None, place_id="") -> dict:
    return {
        "company": re.sub(r"\s+", " ", name).strip(),
        "city": city,
        "lead_source": "OpenStreetMap",
        "phone": _norm_phone(phone),
        "address": address or city,
        "rating": None,
        "maps_rating": None,
        "maps_reviews_count": None,
        "types": types or [],
        "place_id": place_id,
        "contact_name": "",
        "email": email or "",
        "website": website or "",
        "latitude": lat,
        "longitude": lon,
    }


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


def _nominatim_poi_search(query: str, city: str, max_results: int) -> list[dict]:
    """Primary path. Nominatim already returned 200 for Abohar geocode."""
    amenity, _ = _intent(query)
    _throttle_nominatim()
    params = {
        "q": f"{amenity} {city} India",
        "format": "json",
        "limit": max(max_results, 10),
        "addressdetails": 1,
        "extratags": 1,
    }
    try:
        with httpx.Client(timeout=25, headers={"User-Agent": _UA}) as client:
            r = client.get(f"{_NOMINATIM}/search", params=params)
            r.raise_for_status()
            hits = r.json() or []
    except Exception as e:
        print(f"[OSM] Nominatim POI search failed for {city!r}/{query!r}: {e}")
        return []

    out: list[dict] = []
    seen: set[str] = set()
    city_l = (city or "").lower()
    for hit in hits:
        name = (hit.get("name") or hit.get("display_name") or "").split(",")[0].strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        addr = hit.get("address") or {}
        extra = hit.get("extratags") or {}
        loc_city = (addr.get("city") or addr.get("town") or addr.get("village") or "")
        # keep hits in/near the requested city when Nominatim says so
        disp = (hit.get("display_name") or "").lower()
        if city_l and city_l not in disp and loc_city.lower() not in (city_l, ""):
            continue
        cls = (hit.get("class") or "").lower()
        typ = (hit.get("type") or "").lower()
        out.append(_lead(
            name, city,
            lat=float(hit["lat"]) if hit.get("lat") else None,
            lon=float(hit["lon"]) if hit.get("lon") else None,
            address=hit.get("display_name") or "",
            phone=extra.get("phone") or extra.get("contact:phone") or "",
            website=extra.get("website") or extra.get("url") or "",
            email=extra.get("email") or "",
            types=[t for t in (typ, cls, amenity) if t],
            place_id=f"osm:{hit.get('osm_type', 'node')}/{hit.get('osm_id', '')}",
        ))
        if len(out) >= max_results:
            break
    return out


def _overpass_around(query: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    """Cheap node-only around: query. Try mirrors; skip on 504/timeout."""
    _, filt = _intent(query)
    body = (
        "[out:json][timeout:15];\n"
        f"{filt}(around:5000,{lat},{lon});\n"
        "out tags 25;"
    )
    last_err = None
    for url in _OVERPASS_MIRRORS:
        try:
            with httpx.Client(timeout=20, headers={"User-Agent": _UA}) as client:
                r = client.post(url, content=body.encode("utf-8"))
                r.raise_for_status()
                elements = (r.json() or {}).get("elements") or []
        except Exception as e:
            last_err = e
            print(f"[OSM] Overpass {url} failed: {e}")
            continue
        out: list[dict] = []
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
            addr_parts = [
                tags.get("addr:housenumber", ""),
                tags.get("addr:street", ""),
                tags.get("addr:city", "") or city,
            ]
            types = [str(tags[k]).lower() for k in ("amenity", "shop", "office", "tourism") if tags.get(k)]
            out.append(_lead(
                name, city,
                lat=el.get("lat"), lon=el.get("lon"),
                address=", ".join(p for p in addr_parts if p),
                phone=tags.get("phone") or tags.get("contact:phone") or "",
                website=tags.get("website") or tags.get("contact:website") or "",
                email=tags.get("email") or tags.get("contact:email") or "",
                types=types,
                place_id=f"osm:{el.get('type', 'node')}/{el.get('id', '')}",
            ))
            if len(out) >= max_results:
                break
        print(f"[OSM] Overpass {url} -> {len(out)} hits")
        return out
    if last_err:
        print(f"[OSM] all Overpass mirrors failed (last: {last_err})")
    return []


def search_osm_places(
    query: str,
    city: str,
    max_results: int = 20,
) -> list[dict]:
    """Nominatim first (reliable). Overpass around: only if we still need more."""
    results = _nominatim_poi_search(query, city, max_results)
    if len(results) >= max_results:
        return results[:max_results]

    seen = {r["company"].lower() for r in results}
    geo = geocode_city(city)
    extra: list[dict] = []
    if geo:
        lat, lon, _bb = geo
        extra = _overpass_around(query, lat, lon, city, max_results)
    for r in extra:
        if r["company"].lower() in seen:
            continue
        seen.add(r["company"].lower())
        results.append(r)
        if len(results) >= max_results:
            break
    return results[:max_results]


def discovery_maps_provider() -> str:
    return "osm"


def install_osm_maps_fallback() -> None:
    from app.services import lead_discovery as ld

    if getattr(ld, "_osm_fallback_installed", False):
        return

    def search_places(query, city, api_key=None, max_results=20):
        return search_osm_places(query, city, max_results=max_results)

    ld.search_google_maps = search_places  # type: ignore
    ld._osm_fallback_installed = True
    print("[OSM] discovery maps = OpenStreetMap only (Google removed from discovery)")
