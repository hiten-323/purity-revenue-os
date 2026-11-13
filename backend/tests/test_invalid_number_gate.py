"""26 Oct (Hiten-approved): a number already quarantined as invalid is never
dialled by ANY path (root cause: only the ai_call_cycle selector filtered
phone_trust; may_place_ai_call - the single authority - did not)."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import founder_call_pipeline as pipeline
from app.services.call_intelligence import never_rang


def _lead(**kw):
    d = dict(id=1, do_not_call=False, consent_status="", contact_status="", status="NEW",
             phone="+919812345678", lead_locked_until=None, ai_call_count=0,
             last_call_date=None, ai_retry_after=None, phone_trust=None,
             name="Kumar General", business_name="Kumar General", category="kirana")
    d.update(kw); return SimpleNamespace(**d)


@pytest.mark.parametrize("trust", ["SIP_INVALID", "INVALID", "SHARED_QUARANTINE", "sip_invalid"])
def test_quarantined_number_is_never_dialled(trust):
    ok, why = pipeline.may_place_ai_call(_lead(phone_trust=trust))
    assert ok is False and "quarantined" in why


@pytest.mark.parametrize("trust", [None, "UNKNOWN", "VERIFIED"])
def test_unquarantined_number_not_blocked_by_trust_gate(trust):
    ok, why = pipeline.may_place_ai_call(_lead(phone_trust=trust))
    assert "quarantined" not in (why or "")


class _Q:
    def __init__(self, n): self.n = n
    def filter(self, *a): return self
    def count(self): return self.n


class _DB:
    def __init__(self, n): self.n = n
    def query(self, *a): return _Q(self.n)


def test_sip_reject_is_final_rules():
    lead = SimpleNamespace(id=1)
    assert never_rang.sip_reject_is_final(_DB(0), lead, 484) is False   # first strike
    assert never_rang.sip_reject_is_final(_DB(0), lead, 404) is False   # first strike
    assert never_rang.sip_reject_is_final(_DB(1), lead, 404) is True    # 2nd, a day+ later
    assert never_rang.sip_reject_is_final(_DB(1), lead, 484) is True
    assert never_rang.sip_reject_is_final(_DB(0), lead, 400) is False   # first strike
    assert never_rang.sip_reject_is_final(_DB(1), lead, 400) is True    # 2nd, a day+ later
    assert never_rang.sip_reject_is_final(_DB(5), lead, 503) is False
    assert never_rang.sip_reject_is_final(_DB(0), lead, None) is False
