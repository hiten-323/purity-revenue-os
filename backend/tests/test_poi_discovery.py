from app.services.poi_discovery import (
    DiscoveredBusiness,
    build_overpass_query,
    dedupe_key,
    merge_sources,
    osm_record_to_business,
    overture_record_to_business,
)


def test_overture_record_normalizes_current_schema():
    record = {
        "id": "gers-1",
        "geometry": {"coordinates": [77.5946, 12.9716]},
        "names": {"primary": "Example Cafe"},
        "addresses": [{"freeform": "MG Road, Bengaluru"}],
        "basic_category": "cafe",
        "taxonomy": {"primary": "cafe"},
        "phones": ["+91 98765 43210"],
        "websites": ["https://example.com/"],
        "confidence": 0.92,
    }
    business = overture_record_to_business(record)
    assert business is not None
    assert business.name == "Example Cafe"
    assert business.category == "cafe"
    assert business.phone == "+919876543210"
    assert business.source == "overture"
    assert business.source_id == "gers-1"
    assert business.confidence == 0.92


def test_osm_record_normalizes_contact_and_location():
    element = {
        "type": "node",
        "id": 42,
        "lat": 12.9716,
        "lon": 77.5946,
        "tags": {
            "name": "Example Cafe",
            "amenity": "cafe",
            "phone": "+91 98765 43210",
            "website": "https://example.com",
            "addr:street": "MG Road",
        },
    }
    business = osm_record_to_business(element)
    assert business is not None
    assert business.source_id == "node/42"
    assert business.phone == "+919876543210"
    assert business.category == "cafe"


def test_overpass_query_is_bounded_and_deterministic():
    query = build_overpass_query((12.9, 77.5, 13.0, 77.6), ("cafe",))
    assert "12.9,77.5,13.0,77.6" in query
    assert 'amenity"="cafe' in query
    assert 'amenity"="restaurant' not in query


def test_merge_preserves_source_provenance_without_granting_consent():
    overture = overture_record_to_business({
        "id": "gers-1", "geometry": {"coordinates": [77.5946, 12.9716]},
        "names": {"primary": "Example Cafe"}, "phones": ["+919876543210"],
        "confidence": 0.9,
    })
    osm = osm_record_to_business({
        "type": "node", "id": 42, "lat": 12.9716, "lon": 77.5946,
        "tags": {"name": "Example Cafe", "phone": "+919876543210"},
    })
    merged = merge_sources([overture, osm])
    assert len(merged) == 1
    assert merged[0].source in {"overture+osm", "osm+overture"}
    assert dedupe_key(overture) == "gers:gers-1"


# --- cross-provider identity: the defects a green CI run still hid -----------

def test_the_same_number_written_differently_is_one_business():
    """Overture publishes "+91-98765-43210"; OSM publishes "098765 43210".

    normalize_phone used to strip whitespace only, so those produced two
    different keys and phone corroboration never fired. The original tests
    passed because their fixtures shared byte-identical coordinates, so the
    geo key matched instead and hid it.
    """
    from app.services.poi_discovery import phone_key

    forms = ["+91-98765-43210", "098765 43210", "+91 98765 43210",
             "9876543210", "(0)98765-43210"]
    assert len({phone_key(f) for f in forms}) == 1, {
        f: phone_key(f) for f in forms}


def test_phone_corroborates_across_providers_without_matching_coordinates():
    overture = DiscoveredBusiness(
        name="Cafe Blue", latitude=30.7333, longitude=76.7794,
        phone="+91-98765-43210", source="overture", source_id="gers-1")
    osm = DiscoveredBusiness(
        name="Cafe Blue Coffee", latitude=30.7401, longitude=76.7850,
        phone="098765 43210", source="osm", source_id="node/1")

    merged = merge_sources([overture, osm])
    assert len(merged) == 1, "same phone, different providers, did not corroborate"
    assert "overture" in merged[0].source and "osm" in merged[0].source


def test_same_shopfront_eleven_metres_apart_corroborates():
    """Two providers describing one shop do not agree to 1.1 metres."""
    a = DiscoveredBusiness(name="Cafe Blue", latitude=30.73330,
                           longitude=76.77940, source="overture")
    b = DiscoveredBusiness(name="Cafe Blue", latitude=30.73337,
                           longitude=76.77948, source="osm")
    assert len(merge_sources([a, b])) == 1


def test_two_branches_of_a_chain_stay_separate():
    """The other direction, and the one that costs a real prospect.

    Over-merging cannot be undone downstream; a missed merge only leaves a
    duplicate that a later phone or website match will join.
    """
    a = DiscoveredBusiness(name="More Supermarket", latitude=30.7333,
                           longitude=76.7794, source="overture")
    b = DiscoveredBusiness(name="More Supermarket", latitude=30.7355,
                           longitude=76.7820, source="osm")     # ~300 m away
    assert len(merge_sources([a, b])) == 2


def test_different_businesses_at_one_address_stay_separate():
    """A mall food court: same coordinates, different names."""
    a = DiscoveredBusiness(name="Cafe Blue", latitude=30.7333,
                           longitude=76.7794, source="overture")
    b = DiscoveredBusiness(name="Chai Point", latitude=30.7333,
                           longitude=76.7794, source="osm")
    assert len(merge_sources([a, b])) == 2


def test_discovery_cannot_touch_any_gate():
    """Structural, not aspirational: the module imports no session and no model,
    so it has nothing to write consent, trust or DNC to."""
    import ast
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "services", "poi_discovery.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    for forbidden in ("app.models.models", "app.database.database"):
        assert forbidden not in imported, (
            f"poi_discovery imports {forbidden}; discovery must not be able to "
            f"write lead state")

    src = open(path, encoding="utf-8").read()
    body = ast.unparse(tree)
    for token in ("consent_status", "email_trust", "do_not_call", "phone_verified"):
        assert token not in body, f"discovery references {token}"
