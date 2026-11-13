import pytest
from app.services.category_classifier import is_disqualified_non_coffee
from app.services.lead_discovery import classify_business


def test_agricultural_fertilizer_disqualification():
    # Vij Traders in Grain Market
    disq, tag, why = is_disqualified_non_coffee("Vij Traders", address="New Grain Market, Phagwara Road, Hoshiarpur")
    assert disq is True
    assert tag in ("AGRICULTURE_FERTILIZER", "MANDI_NON_FOOD_TRADER")

    # Explicit fertilizer store
    disq, tag, why = is_disqualified_non_coffee("Kisan Fertilizer and Pesticides Store")
    assert disq is True
    assert tag == "AGRICULTURE_FERTILIZER"


def test_hardware_construction_disqualification():
    disq, tag, why = is_disqualified_non_coffee("Kajaria Tiles Authorised Showroom - Best Traders")
    assert disq is True
    assert tag == "HARDWARE_CONSTRUCTION"

    disq, tag, why = is_disqualified_non_coffee("Bansal Steel and Cement Store")
    assert disq is True
    assert tag == "HARDWARE_CONSTRUCTION"


def test_automobile_disqualification():
    disq, tag, why = is_disqualified_non_coffee("National Tyres and Auto Spare Parts")
    assert disq is True
    assert tag == "AUTOMOBILE_SPARE_PARTS"


def test_medical_pharma_disqualification():
    disq, tag, why = is_disqualified_non_coffee("Modern Surgical House - Surgical Products Wholesaler")
    assert disq is True
    assert tag == "MEDICAL_PHARMA"

    disq, tag, why = is_disqualified_non_coffee("Gupta Medical Hall & Chemist")
    assert disq is True
    assert tag == "MEDICAL_PHARMA"


def test_food_and_hospitality_protection():
    # Cafes, Hotels, Restaurants, Grocers, Caterers, Gifting should NOT be disqualified
    disq, _, _ = is_disqualified_non_coffee("The Urban Cup Cafe")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Taj Chandigarh Hotel")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Bistro Flamme Bois Restaurant")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Reliance SMART Superstore")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Sumit Karyana Store")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Soulchef Caterers and Events")
    assert disq is False

    disq, _, _ = is_disqualified_non_coffee("Signature Gift and Hamper Lounge")
    assert disq is False


def test_classify_business_integration():
    # Non-coffee business gets 0 score and disqualified status
    res = classify_business(None, "Zimidara Kheti Store (Pesticide Shop)")
    assert res["coffee_buying_score"] == 0
    assert res["division"] == "non_coffee_retail"
    assert res.get("disqualified") is True

    # Real cafe gets high score
    res = classify_business(["cafe"], "Blue Tokai Coffee Roasters")
    assert res["coffee_buying_score"] >= 90
    assert res["division"] == "cafe"
