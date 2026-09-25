from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services import founder_call_pipeline as p


def test_production_conversation_contract():
    assert p.MAX_AGENT_RESPONSE_WORDS == 15
    assert any("specific date/time" in rule for rule in p.VOICE_CONVERSATION_RULES)
    assert any("again says no" in rule for rule in p.VOICE_CONVERSATION_RULES)


def test_busy_and_waiting_are_retryable_outcomes():
    assert p.OUTCOMES["BUSY"] == p.AI_NO_ANSWER
    assert p.OUTCOMES["WAITING"] == p.AI_NO_ANSWER


def test_callback_parser_preserves_explicit_hour():
    dt = p.parse_callback_datetime("tomorrow 7pm", datetime(2026, 9, 25, 15, 0))
    assert dt == datetime(2026, 9, 26, 19, 0)


def test_profile_discloses_ai_and_brand():
    ok, _ = p.script_discloses(p.OPENING_DISCLOSURE)
    assert ok
