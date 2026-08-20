"""
OpenStreetMap places discovery — five distinct buyer entities.

  cafe          amenity=cafe | shop=coffee | name has cafe/coffee (not internet cafe)
  restaurant    amenity=restaurant | fast_food | dhaba in name — NEVER mixed with cafe
  hotel         tourism=hotel/guest_house
  hospital      amenity=hospital/clinic
  canteen_org   school, college, university, office, factory, canteen, mess, pantry

Google is not used. Overpass.fr first; OSM map API fallback.
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
]
_OVERPASS_TIMEOUT = float(os.getenv("OVERPASS_TIMEOUT_SEC", "12"))
_last_nominatim = 0.0

# Distinct entities. Do not collapse cafe into hospitality.
_CAFE_NAME = re.compile(r"\b(cafes?|cafés?|coffee|espresso|barista|brew)\b", re.I)
_INTERNET_CAFE = re.compile(r"\binternet\s*cafes?\b", re.I)
_RESTAURANT_NAME = re.compile(r"\b(restaurants?|dhaba|dhabha|eatery|eateries|diner)\b", re.I)
_HOTEL_NAME = re.compile(r"\b(hotels?|resorts?|inns?|lodging|guest\s*house|residency)\b", re.I)
_HOSPITAL_NAME = re.compile(r"\b(hospitals?|clinics?|nursing\s*homes?|medical)\b", re.I)
_CANTEEN_NAME = re.compile(
    r"\b(canteen|mess|pantry|cafeteria|hostel|campus)\b", re.I
)

# Overpass union fragments per entity (nwr = node+way+relation)
_ENTITY_QL: dict[str, list[str]] = {
    "cafe": [
        'nwr["amenity"="cafe"]',
        'nwr["shop"="coffee"]',
        'nwr["cuisine"="coffee_shop"]',
        'nwr["name"~"(?i)cafe|café|coffee|espresso|barista"]',
    ],
    "restaurant": [
        'nwr["amenity"="restaurant"]',
        'nwr["amenity"="fast_food"]',
        'nwr["name"~"(?i)restaurant|dhaba|eatery"]',
    ],
    "hotel": [
        'nwr["tourism"~"^(hotel|guest_house|motel)$"]',
        'nwr["amenity"="hotel"]',
        'nwr["name"~"(?i)\\bhotel\\b|\\bresort\\b"]',
    ],
    "hospital": [
        'nwr["amenity"~"^(hospital|clinic)$"]',
        'nwr["name"~"(?i)hospital|nursing home"]',
    ],
    "canteen_org": [
        'nwr["amenity"~"^(school|college|university|canteen)$"]',
        'nwr["amenity"="food_court"]',
        'nwr["office"]',
        'nwr["name"~"(?i)canteen|mess|pantry|cafeteria"]',
    ],
}

_QUERY_TO_ENTITY: list[tuple[re.Pattern, str]] = [
    (re.compile(r"cafe|coffee|espresso|barista", re.I), "cafe"),
    (re.compile(r"restaurant|dhaba|cater|fast\s*food", re.I), "restaurant"),
    (re.compile(r"hotel|lodging|resort|inn|guest", re.I), "hotel"),
    (re.compile(r"hospital|clinic|nursing", re.I), "hospital"),
    (re.compile(
        r"canteen|mess|pantry|school|college|university|hostel|office|corporate|factory|institutional",
        re.I,
    ), "canteen_org"),
]

ENTITIES = ("cafe", "restaurant", "hotel", "hospital", "canteen_org")


def entity_for_query(query: str) -> str:
    q = query or ""
    for rx, ent in _QUERY_TO_ENTITY:
        if rx.search(q):
            return ent
    return ""


def classify_osm_entity(name: str, tags: dict | None, searched: str = "") -> str:
    """Assign exactly one of the five entities. Cafe is never hospitality."""
    tags = tags or {}
    n = name or ""
    amenity = (tags.get("amenity") or "").lower()
    shop = (tags.get("shop") or "").lower()
    tourism = (tags.get("tourism") or "").lower()
    office = tags.get("office")

    if amenity == "cafe" or shop == "coffee" or (
        _CAFE_NAME.search(n) and not _INTERNET_CAFE.search(n)
    ):
        return "cafe"
    if amenity in ("restaurant", "fast_food") or _RESTAURANT_NAME.search(n):
        return "restaurant"
    if tourism in ("hotel", "guest_house", "motel") or amenity == "hotel" or _HOTEL_NAME.search(n):
        return "hotel"
    if amenity in ("hospital", "clinic") or _HOSPITAL_NAME.search(n):
        return "hospital"
    if amenity in ("school", "college", "university", "canteen", "food_court") or office or _CANTEEN_NAME.search(n):
        return "canteen_org"
    if searched in ENTITIES:
        return searched
    return "unknown"


def _is_internet_cafe(name: str) -> bool:
    return bool(_INTERNET_CAFE.search(name or ""))


def _keep_for_entity(entity: str, name: str, tags: dict) -> bool:
    if _is_internet_cafe(name):
        return False
    got = classify_osm_entity(name, tags, searched=entity)
    if entity == "cafe":
        return got == "cafe"
    if entity == "restaurant":
        return got == "restaurant"  # exclude cafes even if tagged restaurant
    if entity == "hotel":
        return got == "hotel"
    if entity == "hospital":
        return got == "hospital"
    if entity == "canteen_org":
        return got == "canteen_org"
    return True


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


def _lead(name: str, city: str, *, entity: str, lat, lon, address="", phone="",
          website="", email="", types=None, place_id="") -> dict:
    types = list(types or [])
    if entity and entity not in types:
        types = [entity] + types
    return {
        "company": re.sub(r"\s+", " ", name).strip(),
        "city": city,
        "lead_source": "OpenStreetMap",
        "phone": _norm_phone(phone),
        "address": address or city,
        "rating": None,
        "maps_rating": None,
        "maps_reviews_count": None,
        "types": types,
        "entity": entity,
        "place_id": place_id,
        "contact_name": "",
        "email": email or "",
        "website": website or "",
        "latitude": lat,
        "longitude": lon,
        "segment": entity,
    }


def _from_el(el: dict, city: str, searched: str) -> Optional[dict]:
    tags = el.get("tags") or {}
    name = (tags.get("name") or tags.get("name:en") or "").strip()
    if not name:
        return None
    entity = classify_osm_entity(name, tags, searched=searched)
    if searched and not _keep_for_entity(searched, name, tags):
        return None
    if el.get("type") == "way" and "center" in (el or {}):
        lat = el["center"].get("lat")
        lon = el["center"].get("lon")
    else:
        lat = el.get("lat")
        lon = el.get("lon")
    types = [str(tags[k]).lower() for k in ("amenity", "shop", "office", "tourism") if tags.get(k)]
    addr = ", ".join(p for p in (
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:city", "") or city,
    ) if p)
    osm_id = f"{el.get('type', 'node')}/{el.get('id', '')}"
    return _lead(
        name, city, entity=entity or searched,
        lat=lat, lon=lon, address=addr,
        phone=tags.get("phone") or tags.get("contact:phone") or "",
        website=tags.get("website") or tags.get("contact:website") or "",
        email=tags.get("email") or tags.get("contact:email") or "",
        types=types, place_id=f"osm:{osm_id}",
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


def _overpass_body(entity: str, lat: float, lon: float, radius_m: int, max_results: int) -> bytes:
    frags = _ENTITY_QL.get(entity) or _ENTITY_QL["canteen_org"]
    parts = [f"  {f}(around:{radius_m},{lat},{lon});" for f in frags]
    return (
        f"[out:json][timeout:{int(_OVERPASS_TIMEOUT)}];\n"
        f"(\n" + "\n".join(parts) + "\n);\n"
        f"out center tags {max(max_results, 40)};"
    ).encode("utf-8")


def _overpass(entity: str, lat: float, lon: float, city: str, max_results: int,
              radius_m: int = 12000) -> list[dict]:
    body = _overpass_body(entity, lat, lon, radius_m, max_results)
    first, rest = _OVERPASS_MIRRORS[0], _OVERPASS_MIRRORS[1:]
    try:
        elements = _overpass_one(first, body)
        print(f"[OSM] Overpass {first} {entity} -> {len(elements)} raw")
    except Exception as e:
        print(f"[OSM] Overpass {first} failed: {e}")
        elements = []
        with ThreadPoolExecutor(max_workers=min(3, len(rest) or 1)) as pool:
            futs = {pool.submit(_overpass_one, u, body): u for u in rest}
            try:
                for fut in as_completed(futs, timeout=_OVERPASS_TIMEOUT + 2):
                    try:
                        elements = fut.result()
                        print(f"[OSM] Overpass {futs[fut]} {entity} -> {len(elements)} raw")
                        break
                    except Exception as e2:
                        print(f"[OSM] Overpass {futs[fut]} failed: {e2}")
            except Exception:
                pass

    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        lead = _from_el(el, city, entity)
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


def _osm_map_bbox(entity: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    d = 0.08
    bbox = f"{lon-d},{lat-d},{lon+d},{lat+d}"
    try:
        with httpx.Client(timeout=25, headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
            r = client.get(_OSM_MAP, params={"bbox": bbox})
            r.raise_for_status()
            ctype = r.headers.get("content-type") or ""
            if "json" not in ctype:
                return []
            elements = (r.json() or {}).get("elements") or []
    except Exception as e:
        print(f"[OSM] map API failed: {e}")
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags") or {}
        name = tags.get("name") or ""
        if not name:
            continue
        if not _keep_for_entity(entity, name, tags):
            continue
        lead = _from_el(el, city, entity)
        if not lead:
            continue
        key = lead["company"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
        if len(out) >= max_results:
            break
    print(f"[OSM] map API {entity} -> {len(out)} hits in {city!r}")
    return out


def _photon(entity: str, lat: float, lon: float, city: str, max_results: int) -> list[dict]:
    qmap = {
        "cafe": "cafe",
        "restaurant": "restaurant",
        "hotel": "hotel",
        "hospital": "hospital",
        "canteen_org": "college",
    }
    word = qmap.get(entity, entity or "cafe")
    try:
        with httpx.Client(timeout=12, headers={"User-Agent": _UA}) as client:
            r = client.get(_PHOTON, params={
                "q": f"{word} {city}",
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
        tags = {p.get("osm_key"): p.get("osm_value")}
        if entity and not _keep_for_entity(entity, name, tags):
            continue
        seen.add(name.lower())
        osm_type = "node" if p.get("osm_type") in (None, "N") else str(p.get("osm_type"))
        out.append(_lead(
            name, city, entity=entity,
            lat=lat2, lon=lon2,
            address=", ".join(x for x in (p.get("street"), p.get("city") or city, p.get("state")) if x),
            types=[t for t in (entity, p.get("osm_value")) if t],
            place_id=f"osm:{osm_type}/{p.get('osm_id', '')}",
        ))
        if len(out) >= max_results:
            break
    print(f"[OSM] Photon {entity} -> {len(out)} within 20km of {city!r}")
    return out


def search_osm_places(
    query: str,
    city: str,
    max_results: int = 20,
    entity: str | None = None,
) -> list[dict]:
    ent = entity or entity_for_query(query) or "cafe"
    geo = geocode_city(city)
    if not geo:
        return []
    lat, lon, _bb = geo

    results = _overpass(ent, lat, lon, city, max_results)
    if len(results) >= min(3, max_results):
        return results[:max_results]

    seen = {r["company"].lower() for r in results}
    for extra in (
        _osm_map_bbox(ent, lat, lon, city, max_results),
        _photon(ent, lat, lon, city, max_results),
    ):
        for r in extra:
            if r["company"].lower() in seen:
                continue
            seen.add(r["company"].lower())
            results.append(r)
            if len(results) >= max_results:
                return results[:max_results]
    return results[:max_results]


def search_osm_entities(city: str, max_per: int = 10) -> dict[str, list[dict]]:
    """One pass per distinct entity. Never merges cafe into restaurant/hotel."""
    return {ent: search_osm_places(ent, city, max_results=max_per, entity=ent) for ent in ENTITIES}


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
    print("[OSM] discovery maps = OpenStreetMap only; cafe ≠ restaurant ≠ hotel ≠ hospital ≠ canteen_org")
