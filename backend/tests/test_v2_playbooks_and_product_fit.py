"""
Comprehensive test suite for Purity Revenue OS V2.0:
- Product-to-Customer-Fit Recommendation Engine (product_fit_engine.py)
- Customer Segmentation Engine (customer_segmentation.py)
- Scenario Playbook Engine (scenario_playbook_engine.py)
- Quotation & Pricing Engine (quotation_sample_engine.py)
- Learning & Continuous Optimization Engine (learning_engine.py)
"""
import pytest
from app.database.database import SessionLocal
from app.models.models import B2BLead
from app.services.product_fit_engine import ProductFitEngine, PRODUCT_CATALOGUE
from app.services.customer_segmentation import (
    CustomerSegmentationEngine,
    SEGMENT_ACTIVE_ONLINE_BUYER, SEGMENT_HORECA, SEGMENT_DISTRIBUTOR,
    SEGMENT_CORPORATE, SEGMENT_COMPETITOR_SWITCH, SEGMENT_GENERIC_DIRECTORY
)
from app.services.scenario_playbook_engine import (
    ScenarioPlaybookEngine, ALL_SCENARIOS,
    SCENARIO_A, SCENARIO_B, SCENARIO_C, SCENARIO_E, SCENARIO_F,
    SCENARIO_G, SCENARIO_H, SCENARIO_I, SCENARIO_J, SCENARIO_O, SCENARIO_P
)
from app.services.quotation_sample_engine import QuotationSampleEngine
from app.services.learning_engine import LearningEngine


class MockLead:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", 101)
        self.company = kwargs.get("company", "Artisan Coffee Roasters")
        self.contact_name = kwargs.get("contact_name", "Arjun Patel")
        self.category = kwargs.get("category", "Cafe")
        self.decision_maker_title = kwargs.get("decision_maker_title", "Head of Procurement")
        self.intent_type = kwargs.get("intent_type", "looking for coffee supplier")
        self.notes = kwargs.get("notes", "")
        self.website = kwargs.get("website", "https://artisancoffee.test")
        self.contact_confidence = kwargs.get("contact_confidence", 0.90)
        self.phone = kwargs.get("phone", "+919876543210")
        self.email = kwargs.get("email", "arjun@artisancoffee.test")
        self.status = kwargs.get("status", "ACTIVE")


# 1. Product Fit Tests
def test_product_fit_arabica_recommends_purica():
    rec = ProductFitEngine.recommend(taste_preference="smooth arabica caramel", price_sensitivity="medium")
    assert "Purica" in rec.primary_product
    assert rec.price in (299.0, 559.0)
    assert rec.primary_sku in ("PB-PCA-50", "PB-PCA-100")


def test_product_fit_strong_zero_chicory_recommends_purista():
    rec = ProductFitEngine.recommend(strength_preference="strong crema", chicory_allowed=False)
    assert "Purista" in rec.primary_product
    assert rec.starter_pack_sku == "PB-GOURMET-DUO-50"
    assert "100% Pure Coffee" in rec.blend_disclosure


def test_product_fit_budget_chicory_recommends_ultra_blend():
    rec = ProductFitEngine.recommend(taste_preference="chicory", price_sensitivity="high")
    assert "Ultra Blend" in rec.primary_product
    assert rec.price in (209.0, 309.0)


def test_product_fit_competitor_davidoff_note():
    rec = ProductFitEngine.recommend(current_competitor="Davidoff")
    assert "Davidoff displacement" in rec.competitor_displacement_note


# 2. Customer Segmentation Tests
def test_segmentation_active_buyer():
    lead = MockLead(intent_type="online enquiry looking for coffee supply")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_ACTIVE_ONLINE_BUYER


def test_segmentation_horeca():
    lead = MockLead(intent_type="", category="Specialty Cafe and Bistro")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_HORECA


def test_segmentation_distributor():
    lead = MockLead(intent_type="", category="FMCG Wholesaler and Distributor")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_DISTRIBUTOR


def test_segmentation_corporate():
    lead = MockLead(intent_type="", category="Corporate Office IT Park")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_CORPORATE


def test_segmentation_competitor_switch():
    lead = MockLead(intent_type="", notes="Currently using Nescafe Classic in pantry")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_COMPETITOR_SWITCH


def test_segmentation_generic_held():
    lead = MockLead(category="General Trading", phone=None, email=None, contact_confidence=0.1, intent_type="")
    seg = CustomerSegmentationEngine.classify(lead)
    assert seg.code == SEGMENT_GENERIC_DIRECTORY
    assert seg.qualification_priority == "HOLD"


# 3. Scenario Playbook Tests
def test_all_16_scenarios_execute():
    lead = MockLead()
    for sc in ALL_SCENARIOS:
        act = ScenarioPlaybookEngine.execute_playbook(sc, lead)
        assert act.scenario_code == sc
        assert act.next_state != ""


def test_scenario_a_active_enquiry_sla():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_A, lead)
    assert act.sla_minutes == 15
    assert act.target_channel == "PHONE"
    assert "https://www.p3online.in/products/variety-box" in act.suggested_copy


def test_scenario_b_distributor_margin_in_copy():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_B, lead)
    assert act.target_channel == "EMAIL"
    assert "30% margin structure" in act.suggested_copy


def test_scenario_f_price_objection_yield():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_F, lead)
    assert "50 cups" in act.suggested_copy
    assert act.action_type == "OBJECTION_RESPONSE"


def test_scenario_h_max_retries_lock():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_H, lead, context={"attempt_count": 3})
    assert act.allowed is False
    assert act.action_type == "HOLD"
    assert act.next_state == "RESTING_COOLDOWN"


def test_scenario_o_human_escalation():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_O, lead)
    assert act.requires_human_alert is True
    assert act.target_channel == "HUMAN"
    assert act.next_state == "HUMAN_INTERVENTION_REQUIRED"


def test_scenario_p_order_conversion():
    lead = MockLead()
    act = ScenarioPlaybookEngine.execute_playbook(SCENARIO_P, lead)
    assert act.allowed is False
    assert act.action_type == "TERMINATE"
    assert act.next_state == "PAID_CONVERSION"


# 4. Quotation & Pricing Tests
def test_quotation_wholesale_calculation():
    q = QuotationSampleEngine.generate_wholesale_quotation("PB-BLD-100", 24, "ADVANCE_OR_COD")
    assert q.quantity_units == 24
    assert q.unit_mrp == 329.0
    assert q.gross_mrp_total == 329.0 * 24
    assert q.base_discount_pct == 30.0
    assert q.base_discount_amount == round(q.gross_mrp_total * 0.30, 2)
    assert q.trade_discount_pct == 25.0
    assert q.trade_discount_amount == round(q.gross_mrp_total * 0.25, 2)
    assert q.cash_discount_pct == 5.0
    assert q.cash_discount_amount == round(q.gross_mrp_total * 0.05, 2)
    assert q.validity_days == 3
    assert q.final_invoice_amount == round(q.gross_mrp_total - q.trade_discount_amount - q.cash_discount_amount, 2)
    assert q.effective_margin_pct == 30.0
    assert "buyer bears actual freight" in q.freight_terms.lower()
    assert q.gst_amount > 0


def test_wholesale_cod_discount_and_three_day_validity():
    q = QuotationSampleEngine.generate_wholesale_quotation("PB-BLD-100", 12, "COD")
    assert q.cash_discount_pct == 5.0
    assert q.cash_discount_amount == round(q.gross_mrp_total * 0.05, 2)
    assert q.validity_days == 3
    assert q.final_invoice_amount == round(q.gross_mrp_total - q.trade_discount_amount - q.cash_discount_amount, 2)


def test_wholesale_below_12_jars_is_flagged_for_founder_approval_not_rejected():
    q = QuotationSampleEngine.generate_wholesale_quotation("PB-BLD-100", 6)
    assert q.quantity_units == 6
    assert q.below_reference_quantity is True
    assert q.founder_approval_required is True
    assert q.approval_status == "PENDING_FOUNDER_APPROVAL"

def test_starter_pack_variety_box_disclosure():
    pack = QuotationSampleEngine.generate_starter_offer("VARIETY_BOX")
    assert pack["price"] == 877.0
    assert "chicory" in pack["blend_disclosure"].lower()


def test_starter_pack_gourmet_duo_zero_chicory():
    pack = QuotationSampleEngine.generate_starter_offer("GOURMET_DUO_MINI")
    assert pack["price"] == 570.0
    assert "Zero Chicory" in pack["blend_disclosure"]


# 5. Learning Engine Tests
def test_learning_report_generation():
    db = SessionLocal()
    try:
        report = LearningEngine.generate_learning_report(db, days_lookback=30)
        assert report.total_leads_tracked > 0
        assert "PHONE" in report.channel_performance
        assert "EMAIL" in report.channel_performance
        assert len(report.optimization_recommendations) > 0
    finally:
        db.close()
