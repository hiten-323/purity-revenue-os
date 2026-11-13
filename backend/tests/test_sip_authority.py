"""23 Oct: voice-server SIP result beats the agent's generic NO_ANSWER."""
from types import SimpleNamespace
from app.services.call_intelligence import capture


class Q:
    def __init__(self, n=0): self.n = n
    def filter(self, *a): return self
    def count(self): return self.n


class DB:
    def __init__(self, n=0): self.n = n
    def query(self, *a): return Q(self.n)
    def flush(self): pass


def _row(**kw):
    d = dict(id=1, lead_id=7, status="FINAL", source="voice_agent", outcome="NO_ANSWER",
             sip_status_code=None, termination_reason="NOT_ANSWERED", evidence={},
             summary="not answered: rang 20s", connected=False, confidence=None)
    d.update(kw); return SimpleNamespace(**d)


def _lead(): return SimpleNamespace(id=7, ai_call_count=2, phone_trust=None,
                                    call_status="NO_ANSWER", ai_retry_after=None)


def _run(monkeypatch, row, reason, code, db=None):
    monkeypatch.setattr(capture, "find_result", lambda db, cid: row)
    lead = _lead()
    out = capture.correct_generic_no_answer(db or DB(), lead, call_ref="ppp-7-x",
                                            reason=reason, sip_status_code=code)
    return out, lead


def test_authoritative_codes():
    assert capture.is_authoritative_sip(486) and capture.is_authoritative_sip(400)
    assert capture.is_authoritative_sip(503)
    assert not capture.is_authoritative_sip(480) and not capture.is_authoritative_sip(None)


def test_busy_wins(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "BUSY", 486)
    assert out["corrected"] == "BUSY" and row.outcome == "BUSY" and row.sip_status_code == 486
    assert lead.ai_call_count == 2  # it rang: attempt stays counted


def test_carrier_400_first_strike_releases_attempt_not_quarantined(monkeypatch):
    # 26 Oct (Hiten-approved): a single SIP 400 is only a strike (6 such leads later rang).
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 400, DB(0))
    assert row.outcome == "DISPATCH_FAILED" and row.sip_status_code == 400
    assert lead.ai_call_count == 1 and lead.phone_trust is None
    assert lead.call_status == "DISPATCH_FAILED"


def test_carrier_400_second_strike_a_day_later_quarantines(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 400, DB(1))
    assert row.outcome == "DISPATCH_FAILED" and lead.phone_trust == "SIP_INVALID"


def test_carrier_484_first_strike_not_quarantined(monkeypatch):
    # 27 Oct (Hiten): 404/484 need a 2nd strike on a different day
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 484, DB(0))
    assert row.outcome == "DISPATCH_FAILED" and lead.phone_trust is None


def test_carrier_484_second_strike_quarantines(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 484, DB(1))
    assert row.outcome == "DISPATCH_FAILED" and lead.phone_trust == "SIP_INVALID"


def test_single_503_not_quarantined(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 503, DB(0))
    assert row.outcome == "DISPATCH_FAILED" and lead.phone_trust is None


def test_403_once_not_quarantined(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 403)
    assert lead.phone_trust is None and lead.ai_call_count == 1


def test_real_ring_out_stays_no_answer(monkeypatch):
    row = _row(); out, lead = _run(monkeypatch, row, "NO_ANSWER", 480)
    assert row.outcome == "NO_ANSWER" and row.sip_status_code == 480


def test_idempotent(monkeypatch):
    row = _row(); _run(monkeypatch, row, "PRE_RING_FAILURE", 400)
    out, lead = _run(monkeypatch, row, "PRE_RING_FAILURE", 400)
    assert out is None  # already corrected; no second attempt release


def test_non_generic_row_untouched(monkeypatch):
    row = _row(outcome="CALLEE_HUNGUP", termination_reason=None)
    out, _ = _run(monkeypatch, row, "BUSY", 486)
    assert out is None and row.outcome == "CALLEE_HUNGUP"
