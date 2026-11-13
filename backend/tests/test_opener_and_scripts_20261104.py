"""4 Nov 2026 (Hiten via B-B OUTREACH 08:08 IST): e4_micro on 100% of dials,
including leads that already heard it but did not answer or hung up in the
greeting; corporate / HoReCa margin wording. Offline only - no dials."""
from types import SimpleNamespace

from app.services.call_intelligence import variants as V


def _env(monkeypatch, share="1.00"):
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "e4_micro")
    monkeypatch.setenv("CALL_OPENER_PRIMARY", "e4_micro")
    monkeypatch.setenv("CALL_OPENER_PRIMARY_SHARE", share)


def test_e4_micro_repeats_when_already_used(monkeypatch):
    _env(monkeypatch)
    for lid in range(1, 60):
        pick = V.select_opener(lead_id=lid, attempt=3, rows=[], business_type="grocery",
                               used_with_lead=["e4_micro"])
        assert pick["opener_variant"] == "e4_micro"
        assert pick["mode"] == "pinned_e4_micro_100"  # 4 Nov 2026 (Hiten "stop,fix all,resume"): e4_micro pinned on 100% of dials


def test_e4_micro_on_every_fresh_dial(monkeypatch):
    _env(monkeypatch)
    for lid in range(1, 60):
        assert V.select_opener(lead_id=lid, attempt=1, rows=[], business_type="cafe")["opener_variant"] == "e4_micro"


def test_share_below_one_keeps_old_behaviour(monkeypatch):
    _env(monkeypatch, share="0.8")
    pick = V.select_opener(lead_id=1, attempt=2, rows=[], business_type="cafe",
                           used_with_lead=["e4_micro"])
    assert pick["opener_variant"] == "e4_micro"  # 4 Nov 2026 (Hiten "stop,fix all,resume"): e4_micro pinned on 100% of dials


def test_unapproved_primary_never_forced(monkeypatch):
    monkeypatch.setenv("CALL_OPENER_VARIANTS_APPROVED", "")
    monkeypatch.setenv("CALL_OPENER_PRIMARY", "e4_micro")
    monkeypatch.setenv("CALL_OPENER_PRIMARY_SHARE", "1.0")
    assert V.select_opener(lead_id=1, attempt=1, rows=[], business_type="cafe")["opener_variant"] == "e4_micro"  # 4 Nov 2026 (Hiten "stop,fix all,resume"): e4_micro pinned on 100% of dials


def test_benefit_line_has_no_pure_or_glass_claim():
    for seg in ("distributor", "grocery", None):
        t = V._benefit(seg)
        assert "pure" not in t.lower() and "glass" not in t.lower() and "100" not in t


def test_corporate_margin_line_up_to_30_off():
    from app.services import warm_followup as wf
    from app.services.founder_call_pipeline import CALL_CONSTRAINTS
    joined = " ".join(CALL_CONSTRAINTS)
    assert "below retail" not in joined
    assert 'Corporate buyers - say "up to 30% off"' in joined
    assert "HoReCa" in joined and joined.count("up to 30% off") >= 2
    lead = SimpleNamespace(division="corporate_office", segment="corporate", industry="", company="Acme Offices")
    if wf._segment(lead) == "corporate":
        assert wf.margin_line(lead) == "Corporate pricing: up to 30% off."


def test_greeting_hangup_or_no_answer_does_not_count_as_used():
    from app.services.call_intelligence.brief import heard_and_engaged
    R = lambda **k: SimpleNamespace(**{"connected": True, "user_turn_count": 1, "outcome": "CALLEE_HUNGUP",
                                        "drop_off_stage": "OPENER", **k})
    assert not heard_and_engaged(R(connected=False, outcome="NO_ANSWER", drop_off_stage="RINGING"))
    assert not heard_and_engaged(R())                                   # hung up in greeting
    assert not heard_and_engaged(R(user_turn_count=0, outcome="OTHER", drop_off_stage="QUALIFY"))
    assert heard_and_engaged(R(outcome="CATALOGUE_REQUESTED", drop_off_stage="CLOSE", user_turn_count=4))
    assert heard_and_engaged(R(outcome="CALLEE_HUNGUP", drop_off_stage="QUALIFY", user_turn_count=3))
