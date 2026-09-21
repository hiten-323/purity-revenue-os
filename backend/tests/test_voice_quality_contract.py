"""Voice-quality rules must travel with every dial payload."""
from __future__ import annotations

from app.services import voice_quality
from app.services.founder_call_pipeline import CALL_CONSTRAINTS


def test_voice_quality_rules_are_defined():
    text = " ".join(voice_quality.VOICE_QUALITY_CONSTRAINTS).lower()
    assert "helpful indian business caller" in text
    assert "one or two short sentences" in text
    assert "do not deliberately add long silent pauses" in text
    assert "warm, calm, confident conversational tone" in text
    assert "pronounce product and company names clearly" in text


def test_dispatch_merges_commercial_and_voice_quality():
    merged = voice_quality.constraints_for_dispatch(CALL_CONSTRAINTS)
    blob = " ".join(merged).lower()
    assert "never state a price" in blob
    assert "helpful indian business caller" in blob
    assert merged[: len(CALL_CONSTRAINTS)] == list(CALL_CONSTRAINTS)
