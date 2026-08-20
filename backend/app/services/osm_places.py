"""
OpenStreetMap places discovery — the ONLY maps provider for lead discovery.

Order (fail fast; do not wait on 504 hosts):
  1. overpass.openstreetmap.fr  (measured ~1s for Abohar hospitality)
  2. other Overpass mirrors in parallel, short timeout
  3. OSM map API bbox (api.openstreetmap.org) — no Overpass, ~2s
  4. Photon with 20 km hard filter

cafe/coffee expands to cafe|restaurant|fast_food: Abohar has restaurants
in OSM, zero cafe nodes. Returning 0 would fake an empty market.
"""
from __future__ import annotations

import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import httpx

_UA = os.getenv(
    "OSM_USER_AGENT",
    "PurityRevenueOS/1.0 (B2B lead discovery; contact=connect@purepantryprovisions.com)",
)
_NOMINATIM = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
_PHOTON = os.getenv("PHOTON_URL", "https://photon.komoot.io/api/").rstrip("/") + "/"
_OSM_MAP = os.getenv("OSM_MAP_URL", "https://api.openstreetmap.org/api/0.6/map")
_OVERPASS_MIRRORS = [
    u.strip() for u in (os.getenv("OVERPASS_URL") or "").split(",") if u.strip()
] or [
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_OVERPASS_TIMEOUT = float(os.getenv("OVERPASS_TIMEOUT_SEC", "12"))

_last_nominatim = 0.0

_HOSPITALITY = {"cafe", "restaurant", "fast_food"}
_HOTEL = {"hotel", "guest_house", "motel"}
_GROCERY = {"supermarket", "convenience", "grocery", "general", "department_store", "marketplace"}
_SCHOOL = {"school", "college", "university"}

_INTENT: list[tuple[re.Pattern, set[str], str]] = [
    (re.compile(r"cafe|coffee|restaurant|dhaba|cater", re.I), _HOSPITALITY, "amenity"),
    (re.compile(r"hotel|lodging|resort", re.I), _HOTEL, "tourism"),
    (re.compile(r"grocery|kirana|supermarket|retail|mart", re.I), _GROCERY, "shop"),
    (re.compile(r"hospital|clinic|nursing", re.I), {"hospital"}, "amenity"),
    (re.compile(r"school|college|university|hostel", re.I), _SCHOOL, "amenity"),
    (re.compile(r"office|corporate|cowork", re.I), set(), "office"),
    (re.compile(r"distributor|wholesale|warehouse|fmcg", re.I), {"wholesale"}, "shop"),
]


def _intent(query: str) -> tuple[set[str], str]:
    for rx, values, key in _INTENT:
        if rx.search(query or ""):
            return values, key
    return set(), ""


def _overpass_filter(query: str) -> str:
    values, key = _intent(query)
    if key == "office":
        return 'node["name"]["office"]'
    if values and key:
        alt = "|".join(sorted(values))
        return f'node["name"]["{key}"~"^({alt})$"]'
    return 'node["name"]["amenity"]'


def _keep_tags(query: str, tags: dict) -> bool:
    values, key = _intent(query)
    if not key:
        return bool(tags.get("amenity") or tags.get("shop") or tags.get("tourism") or tags.get("office"))
    if key == "office":
        return bool(tags.get("office"))
    raw = (tags.get(key) or tags.get("amenity") or tags.get("shop") or tags.get("tourism") or "").lower()
    return raw in values


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


def _km(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return 9999.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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


def _from_tags(el_or_tags, city: str, lat=None, lon=None, osm_id="") -> Optional[dict]:
    if isinstance(el_or_tags, dict) and "tags" in el_or_tags:
        el = el_or_tags
        tags = el.get("tags") or {}
        lat = el.get("lat", lat)
        lon = el.get("lon", lon)
        osm_id = f"{el.get('type', 'node')}/{el.get('id', '')}"
    else:
        tags = el_or_tags or {}
    name = (tags.get("name") or tags.get("name:en") or "").strip()
    if not name:
        return None
    types = [str(tags[k]).lower() for k in ("amenity", "shop", "office", "tourism") if tags.get(k)]
    addr = ", ".join(p for p in (
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:city", "") or city,
    ) if p)
    return _lead(
        name, city, lat=lat, lon=lon, address=addr,
        phone=tags.get("phone") or tags.get("contact:phone") or "",
        website=tags.get("website") or tags.get("contact:website") or "",
        email=tags.get("email") or tags.get("contact:email") or "",
        types=types, place_id=f"osm:{osm_id}" if osm_id else "",
    )


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
                d = 0.10
                south, north, west, east = lat - d, lat + d, lon - d, lon + d
            return lat, lon, {"south": south, "north": north, "west": west, "east": east}
    except Exception as e:
        print(f"[OSM] Nominatim geocode failed for {city!r}: {e}")
        return None


def _overpass_one(url: str, body: bytes) -> list[dict]:
    with httpx.Client(timeout=_OVERPASS_TIMEOUT, headers={"User-Agent": _UA}) as client:
        r = client.post(url, content=body)
        r.raise_for_status()
        return (r.json() or {}).get("elements") or []


def _overpass(query: str, lat: float, lon: float, city: str, max_results: int,
              radius_m: int = 12000) -> list[dict]:
    filt = _overpass_filter(query)
    body = (
        f"[out:json][timeout:{int(_OVERPASS_TIMEOUT)}];\n"
        f"{filt}(around:{radius_m},{lat},{lon});\n"
        f"out tags {max(max_results, 40)};"
    ).encode("utf-8")

    # First mirror sequentially (usually fr, ~1s). Rest in parallel if needed.
    first, rest = _OVERPASS_MIRRORS[0], _OVERPASS_MIRRORS[1:]
    try:
        elements = _overpass_one(first, body)
        print(f"[OSM] Overpass {first} -> {len(elements)} raw")
    except Exception as e:
        print(f"[OSM] Overpass {first} failed: {e}")
        elements = []
        with ThreadPoolExecutor(max_workers=min(4, len(rest) or 1)) as pool:
            futs = {pool.submit(_overpass_one, u, body): u for u in rest}
            for fut in as_completed(futs, timeout=_OVERPASS_TIMEOUT + 2):
                try:
                    elements = fut.result()
                    print(f"[OSM] Overpass {futs[fut]} -> {len(elements)} raw")
                    break
                except Exception as e2:
                    print(f"[OSM] Overpass {futs[fut]} failed: {e2}")

    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        lead = _from_tags(el, city)
        if not lead:
            continue
        key = lead["company"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
        if len(out) >= max_results:
            break
    return out


def _osm_map_bbox(query: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    """Main OSM editing API — no Overpass. Small bbox only."""
    d = 0.08  # ~9 km
    bbox = f"{lon-d},{lat-d},{lon+d},{lat+d}"
    try:
        with httpx.Client(timeout=25, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
            r = client.get(_OSM_MAP, params={"bbox": bbox})
            r.raise_for_status()
            payload = r.json() if "json" in (r.headers.get("content-type") or "") else None
            if payload is None:
                # XML fallback is large; skip if not JSON
                return []
            elements = payload.get("elements") or []
    except Exception as e:
        print(f"[OSM] map API failed: {e}")
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        if el.get("type") not in ("node", None):
            tags = el.get("tags") or {}
            if not tags.get("name"):
                continue
        tags = el.get("tags") or {}
        if not _keep_tags(query, tags):
            continue
        lead = _from_tags(el, city)
        if not lead:
            continue
        key = lead["company"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
        if len(out) >= max_results:
            break
    print(f"[OSM] map API -> {len(out)} hits for {city!r}/{query!r}")
    return out


def _photon(query: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    amenity_word = re.split(r"\s+", (query or "cafe").strip())[0]
    try:
        with httpx.Client(timeout=12, headers={"User-Agent": _UA}) as client:
            r = client.get(_PHOTON, params={
                "q": f"{amenity_word} {city}",
                "lat": lat, "lon": lon, "limit": max(max_results * 3, 15),
            })
            r.raise_for_status()
            feats = (r.json() or {}).get("features") or []
    except Exception as e:
        print(f"[OSM] Photon failed: {e}")
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for f in feats:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        lon2, lat2 = coords[0], coords[1]
        if _km(lat, lon, lat2, lon2) > 20:
            continue
        name = (p.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        types = [t for t in (p.get("osm_value"), p.get("osm_key")) if t]
        osm_type = "node" if p.get("osm_type") in (None, "N") else str(p.get("osm_type"))
        out.append(_lead(
            name, city, lat=lat2, lon=lon2,
            address=", ".join(x for x in (p.get("street"), p.get("city") or city, p.get("state")) if x),
            types=types, place_id=f"osm:{osm_type}/{p.get('osm_id', '')}",
        ))
        if len(out) >= max_results:
            break
    print(f"[OSM] Photon -> {len(out)} hits within 20km of {city!r}")
    return out


def search_osm_places(
    query: str,
    city: str,
    max_results: int = 20,
) -> list[dict]:
    geo = geocode_city(city)
    if not geo:
        return []
    lat, lon, _bb = geo

    results = _overpass(query, lat, lon, city, max_results)
    if len(results) >= min(3, max_results):
        return results[:max_results]

    seen = {r["company"].lower() for r in results}
    for extra in (
        _osm_map_bbox(query, lat, lon, city, max_results),
        _photon(query, lat, lon, city, max_results),
    ):
        for r in extra:
            if r["company"].lower() in seen:
                continue
            seen.add(r["company"].lower())
            results.append(r)
            if len(results) >= max_results:
                return results[:max_results]
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
