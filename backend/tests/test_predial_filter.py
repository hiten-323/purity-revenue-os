"""27 Oct 2026: pre-dial filter (dead +91 series and non-mobile numbers)."""
from types import SimpleNamespace

import pytest

from app.services import founder_call_pipeline as pipeline


def _lead(**kw):
    d = dict(id=1, do_not_call=False, consent_status="", contact_status="", status="NEW",
             phone="+919812345678", lead_locked_until=None, ai_call_count=0,
             last_call_date=None, ai_retry_after=None, phone_trust=None,
             name="Kumar General", business_name="Kumar General", category="kirana")
    d.update(kw); return SimpleNamespace(**d)


@pytest.mark.parametrize("phone", ["+916060000001", "916106000002", "06144000003", "6968000004",
                                   "+91-6968-000-004"])
def test_dead_series_blocked(phone):
    assert pipeline.predial_block(phone) == "DEAD_SERIES"
    ok, why = pipeline.may_place_ai_call(_lead(phone=phone))
    assert ok is False and "DEAD_SERIES" in why


@pytest.mark.parametrize("phone", [None, "", "+91 1800 891 0001", "1800891000", "18605001234",
                                   "+911900123456", "12345", "+9198765432", "+12025550123",
                                   "+910123456789", "0172-501987"])
def test_invalid_numbers(phone):
    assert pipeline.predial_block(phone) == "INVALID_NUMBER"
    ok, _ = pipeline.may_place_ai_call(_lead(phone=phone))
    assert ok is False


@pytest.mark.parametrize("phone", ["+919812345678", "7012345678", "+916012345678",
                                   "+916252718340", "+916061482937"])
def test_valid_mobiles_pass_filter(phone):
    # Pure filter only: past this gate may_place_ai_call reaches DB-backed
    # checks that need a real session, covered by the existing suites.
    assert pipeline.predial_block(phone) is None


def test_mirrors_sidecar_prefixes():
    assert pipeline.DEAD_NUMBER_SERIES == ("6060", "6106", "6144", "6968")


@pytest.mark.parametrize("phone,e164", [("0172-5019876", "+911725019876"), ("022 2834 5912", "+912228345912"),
                                        ("+91-16124-12345", "+911612412345"), ("911752123473", "+911752123473"),
                                        ("9814012345", "+919814012345")])
def test_landlines_dialable_and_normalised(phone, e164):
    from app.services import identity
    assert identity.voice_dialable_e164(phone) == e164
    assert identity.is_voice_dialable(phone) is True
    assert pipeline.predial_block(phone) is None


@pytest.mark.parametrize("phone", ["+91 1800 891 0001", "1860-500-1234", "1900123456"])
def test_toll_free_and_premium_refused(phone):
    from app.services import identity
    assert identity.is_voice_dialable(phone) is False
    ok, why = pipeline.may_place_ai_call(_lead(phone=phone))
    assert ok is False and "voice rule" in why


def test_queue_builder_uses_landline_aware_rule():
    import inspect
    from app.services import ai_call_cycle
    src = inspect.getsource(ai_call_cycle)
    assert "is_voice_dialable_mobile(" not in src
    assert src.count("identity.is_voice_dialable(l.phone)") == 3
