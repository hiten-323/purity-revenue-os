from app.services.poi_discovery import (
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
