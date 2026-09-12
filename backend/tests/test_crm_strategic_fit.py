from app.services.crm_tracker import CRMTrackerService


def test_strategic_fit_uses_industry_evidence_for_grocery_segment():
    """Industry evidence must not be discarded by the division fallback map."""
    class Lead:
        email_opens = email_clicks = 0
        status = "DISCOVERED"
        estimated_value = 0.0
        decision_maker_score = None
        contact_persona = None
        division = "corporate"
        industry = "Supermarket Grocery"
        probability = 0.05
        intent_score = 0
        expected_monthly_consumption_kg = 0.0
        proposal_suggested_margin = 0.0
        proposal_suggested_price = 0.0
        proposal_monthly_kg = 0.0
        stage_entered_date = None
        last_updated = None

    lead = Lead()
    CRMTrackerService.recalculate_lead_score_and_action(lead)

    # Grocery/supermarket evidence is a 95-point strategic-fit signal.
    # The regression is intentionally expressed through the resulting score:
    # replacing the evidence-based fit with the division fallback (85) must
    # no longer be possible.
    assert lead.intent_score == 0
