"""
OpenStreetMap places discovery — the ONLY maps provider for lead discovery.

Primary: Overpass around: (node + name) on public mirrors. overpass-api.de 504s;
maps.mail.ru has been the reliable host for India POIs.
Secondary: Photon (komoot) with lat/lon bias + hard distance filter.
Tertiary: Nominatim POI search (weak for small Indian towns).

Honest limits: ratings NULL; phones/websites only if OSM-tagged.
"""
from __future__ import annotations

import math
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
_PHOTON = os.getenv("PHOTON_URL", "https://photon.komoot.io/api/").rstrip("/") + "/"
_OVERPASS_MIRRORS = [
    u.strip() for u in (os.getenv("OVERPASS_URL") or "").split(",") if u.strip()
] or [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

_last_nominatim = 0.0

# query fragment -> Overpass node filter (named POIs only)
# cafe/coffee expands to hospitality: OSM in small Indian towns often tags
# restaurants, not cafes. Returning 0 for "cafe" when restaurants exist is a
# false empty market — same class of bug Google REQUEST_DENIED caused.
_INTENT: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cafe|coffee|restaurant|dhaba|cater", re.I),
     'node["name"]["amenity"~"^(cafe|restaurant|fast_food)$"]'),
    (re.compile(r"hotel|lodging|resort", re.I),
     'node["name"]["tourism"~"^(hotel|guest_house)$"]'),
    (re.compile(r"grocery|kirana|supermarket|retail|mart", re.I),
     'node["name"]["shop"~"^(supermarket|convenience|grocery|general|department_store)$"]'),
    (re.compile(r"hospital|clinic|nursing", re.I),
     'node["name"]["amenity"="hospital"]'),
    (re.compile(r"school|college|university|hostel", re.I),
     'node["name"]["amenity"~"^(school|college|university)$"]'),
    (re.compile(r"office|corporate|cowork", re.I),
     'node["name"]["office"]'),
    (re.compile(r"distributor|wholesale|warehouse|fmcg", re.I),
     'node["name"]["shop"~"^(wholesale|yes)$"]'),
]

_BROAD = (
    'node["name"]["amenity"~"^(cafe|restaurant|fast_food|hospital|school|college|university)$"]',
    'node["name"]["shop"]',
    'node["name"]["tourism"~"^(hotel|guest_house)$"]',
    'node["name"]["office"]',
)


def _overpass_filters(query: str) -> list[str]:
    for rx, filt in _INTENT:
        if rx.search(query or ""):
            return [filt]
    return list(_BROAD)


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


def _from_overpass_el(el: dict, city: str) -> Optional[dict]:
    tags = el.get("tags") or {}
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
        name, city,
        lat=el.get("lat"), lon=el.get("lon"),
        address=addr,
        phone=tags.get("phone") or tags.get("contact:phone") or "",
        website=tags.get("website") or tags.get("contact:website") or "",
        email=tags.get("email") or tags.get("contact:email") or "",
        types=types,
        place_id=f"osm:{el.get('type', 'node')}/{el.get('id', '')}",
    )


def _overpass(query: str, lat: float, lon: float, city: str, max_results: int,
              radius_m: int = 12000) -> list[dict]:
    filts = _overpass_filters(query)
    union = "\n".join(f"  {f}(around:{radius_m},{lat},{lon});" for f in filts)
    body = f"[out:json][timeout:18];\n(\n{union}\n);\nout tags {max(max_results, 40)};"
    last_err = None
    for url in _OVERPASS_MIRRORS:
        try:
            with httpx.Client(timeout=22, headers={"User-Agent": _UA}) as client:
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
            lead = _from_overpass_el(el, city)
            if not lead:
                continue
            key = lead["company"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(lead)
            if len(out) >= max_results:
                break
        print(f"[OSM] Overpass {url} -> {len(out)} hits for {city!r}/{query!r}")
        return out
    if last_err:
        print(f"[OSM] all Overpass mirrors failed (last: {last_err})")
    return []


def _photon(query: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    """Komoot Photon — OSM search. Must be within 20km of the city or we drop it."""
    amenity_word = re.split(r"\s+", (query or "cafe").strip())[0]
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": _UA}) as client:
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
    city_l = (city or "").lower()
    for f in feats:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        lon2, lat2 = coords[0], coords[1]
        if _km(lat, lon, lat2, lon2) > 20:
            continue
        name = (p.get("name") or "").strip()
        if not name or name.lower() in seen:
            continue
        feat_city = (p.get("city") or p.get("district") or "").lower()
        if feat_city and city_l and feat_city != city_l and city_l not in feat_city:
            # keep if distance already passed — nearby towns are ok within 20km
            pass
        seen.add(name.lower())
        types = [t for t in (p.get("osm_value"), p.get("osm_key")) if t]
        osm_type = "node" if p.get("osm_type") in (None, "N") else str(p.get("osm_type"))
        out.append(_lead(
            name, city,
            lat=lat2, lon=lon2,
            address=", ".join(x for x in (p.get("street"), p.get("city") or city, p.get("state")) if x),
            types=types,
            place_id=f"osm:{osm_type}/{p.get('osm_id', '')}",
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
    for extra in (_photon(query, lat, lon, city, max_results),
                  _overpass("", lat, lon, city, max_results) if query else []):
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
