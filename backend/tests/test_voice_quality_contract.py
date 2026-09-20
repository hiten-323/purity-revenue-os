from app.services.founder_call_pipeline import CALL_CONSTRAINTS


def test_voice_quality_rules_are_sent_with_every_call_context():
    text = " ".join(CALL_CONSTRAINTS).lower()
    assert "helpful indian business caller" in text
    assert "one or two short sentences" in text
    assert "do not deliberately add long silent pauses" in text
    assert "warm, calm, confident conversational tone" in text
    assert "pronounce product and company names clearly" in text
