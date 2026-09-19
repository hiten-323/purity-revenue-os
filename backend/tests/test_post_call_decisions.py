"""
What happens AFTER a founder call -- the half of the funnel that turns a
started conversation into the next one.

Every test here pins a defect found on 2026-09-19 by tracing all 19 Power Hour
buttons through the real code path. None of them was caught by the existing
526 tests, because nothing tested these behaviours: the full suite passed both
before and after the fixes. That is the reason this file exists.

The production database had zero founder calls logged when these were found,
so none of the defects had cost a lead yet. They would have from the first
real call -- which is also the first day SMART_OUTREACH_ENABLED=1 is deployed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.models import ActionQueue, B2BLead, Base, WorkflowEvent
from app.services import outreach_search as osr
from conftest import memory_engine


@pytest.fixture()
def db():
    eng = memory_engine()
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    try:
        yield s
    finally:
        s.close()


def _lead(db, **kw):
    base = dict(company="Test Cafe", city="Bathinda", phone="+91 98140 12345",
                segment="horeca", status="DISCOVERED")
    base.update(kw)
    lead = B2BLead(**base)
    db.add(lead)
    db.commit()
    return lead


def _pending(db, lead_id):
    return db.query(ActionQueue).filter(
        ActionQueue.lead_id == lead_id, ActionQueue.status == "PENDING").all()


def _make_due(db, lead_id):
    for a in _pending(db, lead_id):
        a.due_date = datetime.utcnow() - timedelta(minutes=5)
    db.commit()


# ─── one implementation, in every process ────────────────────────────────────

def test_call_outcome_single_implementation():
    """main.py's startup hook used to swap apply_call_outcome for a second,
    separately maintained copy. The API ran the copy; tests and every other
    process ran the original, so a fix could pass here and never reach
    production. After the hook runs, the function must still be this one."""
    from app.services import call_outcome_failclosed

    before = osr.apply_call_outcome
    call_outcome_failclosed.install()
    assert osr.apply_call_outcome is before
    assert osr.apply_call_outcome.__module__ == "app.services.outreach_search"


def test_registry_matches_engine():
    """The call console tells the founder what each button triggers. It had
    drifted from what the engine actually queues on 15 of 19 buttons."""
    drift = {k: (o.next_action, osr.engine_action_for(k))
             for k, o in osr.OUTCOMES.items()
             if o.next_action != osr.engine_action_for(k)}
    assert not drift, f"registry says / engine does: {drift}"


# ─── every conversation leaves exactly one next step ─────────────────────────

VERDICTS = {"NOT_INTERESTED", "DO_NOT_CONTACT", "WRONG_NUMBER"}


@pytest.mark.parametrize("key", [k for k in osr.OUTCOMES if k not in VERDICTS])
def test_every_non_verdict_outcome_leaves_exactly_one_action(db, key):
    lead = _lead(db)
    osr.apply_call_outcome(db, lead, key, {})
    assert len(_pending(db, lead.id)) == 1, (
        f"{key} left {len(_pending(db, lead.id))} next actions -- a started "
        f"conversation with no next step is a lost lead")


def test_existing_supplier_schedules_a_comparison_callback(db):
    """The most common B2B objection used to queue NOTHING and leave the lead
    DISCOVERED, as if the call had never happened. The founder's own playbook
    (the registry note) says: not a rejection, offer a comparison."""
    lead = _lead(db)
    r = osr.apply_call_outcome(db, lead, "EXISTING_SUPPLIER",
                               {"current_supplier": "Nescafe via local distributor"})
    assert r["next_action"] == "SCHEDULE_CALLBACK"
    (a,) = _pending(db, lead.id)
    days = (a.due_date - datetime.utcnow()).total_seconds() / 86400
    assert 2.9 < days <= 3.0, "delay comes from the registry entry (3 days)"
    assert lead.current_supplier == "Nescafe via local distributor"


# ─── a wrong number is not dialled again ─────────────────────────────────────

def test_wrong_number_clears_the_number_through_power_hour(db):
    lead = _lead(db, phone="+91 98140 12345", whatsapp_number="+919814012345")
    osr.apply_call_outcome(db, lead, "WRONG_NUMBER", {})
    db.refresh(lead)
    assert not lead.phone
    assert not lead.whatsapp_number, (
        "same digits stored as WhatsApp -- Power Hour dials phone OR whatsapp, "
        "so leaving it here means dialling the wrong number again")
    assert not osr._has_phone(lead)
    ev = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id,
                                        WorkflowEvent.event_type == "FOUNDER_CALL").one()
    assert ev.payload["cleared_numbers"], "the cleared value must be kept, not discarded"


def test_wrong_number_keeps_a_different_whatsapp_number(db):
    lead = _lead(db, phone="+91 98140 12345", whatsapp_number="+91 99150 67890")
    osr.apply_call_outcome(db, lead, "WRONG_NUMBER", {})
    db.refresh(lead)
    assert not lead.phone
    assert lead.whatsapp_number == "+91 99150 67890"


def test_log_call_door_clears_the_same_fields(db):
    """Both call-logging doors claim to behave identically. log_call cleared
    phone only; a matching whatsapp_number survived and kept the lead in
    Power Hour."""
    from app.services.phone_intelligence import log_call
    lead = _lead(db, phone="+91 98140 12345", whatsapp_number="+919814012345")
    log_call(lead, db, "WRONG_NUMBER", notes="not them")
    db.commit()
    db.refresh(lead)
    assert not lead.phone and not lead.whatsapp_number
    assert osr.founder_marked_wrong(db, lead.id, "98140 12345")


def test_enrichment_does_not_write_a_rejected_number_back(db, monkeypatch):
    """Cleaning a row is useless if the next enrichment pass restores it --
    the empty field reads as 'missing' and the same directory supplies the
    same number."""
    from app.api import endpoints
    from app.services import contact_enricher

    lead = _lead(db, phone="+91 98140 12345")
    osr.apply_call_outcome(db, lead, "WRONG_NUMBER", {})

    monkeypatch.setattr(contact_enricher, "enrich_lead_contact",
                        lambda *a, **k: {"confirmed_phone": "+91-98140-12345",
                                         "whatsapp_number": "+919814012345",
                                         "email": "", "sources_checked": ["x"],
                                         "confidence": "HIGH", "all_phones_found": []})
    endpoints._enrich_contacts_sync([lead.id], db)
    db.refresh(lead)
    assert not lead.phone, "enrichment restored a number the founder rejected"
    assert not lead.whatsapp_number


# ─── someone who said no is not called again ─────────────────────────────────

def test_closed_lost_is_suppressed_from_every_method(db):
    lead = _lead(db)
    osr.apply_call_outcome(db, lead, "NOT_INTERESTED", {})
    db.refresh(lead)
    assert lead.status == "CLOSED_LOST"
    assert osr.methods_for(lead, {}) == set()
    # Even quiet for weeks -- the old ranking scored this UP.
    assert osr.rank_calls([lead], {lead.id: {"last_touch_days": 30}}) == []


# ─── Power Hour keeps the promises it made ───────────────────────────────────

def _cold_star(db, i):
    """A cold lead with every bonus rank_calls can give a stranger."""
    return _lead(db, company=f"Cold Star {i}", phone=f"+91 98765 4{i:04d}",
                 decision_maker="Owner", current_supplier="X",
                 phone_verified=True, maps_reviews_count=5000,
                 estimated_value=10_000_000)


def test_a_due_callback_outranks_every_cold_lead(db):
    colds = [_cold_star(db, i) for i in range(20)]
    lead = _lead(db, company="Owed a callback")
    osr.apply_call_outcome(db, lead, "CALL_LATER", {"next_followup_date": "today"})
    _make_due(db, lead.id)

    everyone = colds + [lead]
    ev = {c.id: {"replied": True} for c in colds}
    ranked = osr.rank_calls(everyone, ev, pending=osr.pending_calls(db))
    assert ranked[0]["lead_id"] == lead.id
    assert ranked[0]["callback_due"] is True


def test_a_callback_not_yet_due_is_held_back(db):
    """'Call me Thursday' means not Wednesday."""
    lead = _lead(db)
    osr.apply_call_outcome(db, lead, "CALL_LATER", {"next_followup_date": "in a week"})
    ranked = osr.rank_calls([lead], {}, pending=osr.pending_calls(db))
    assert ranked == []


def test_promises_are_ordered_by_what_is_at_stake(db):
    order = []
    for key in ("NO_ANSWER", "DECISION_MAKER_FOUND", "CALL_LATER", "INTERESTED"):
        lead = _lead(db, company=key, phone=f"+91 9{len(order)}140 12345")
        osr.apply_call_outcome(db, lead, key, {})
        _make_due(db, lead.id)
        order.append(lead)
    ranked = osr.rank_calls(order, {}, pending=osr.pending_calls(db))
    assert [r["commitment"] for r in ranked] == [
        "FOUNDER_CALL", "SCHEDULE_CALLBACK", "CALL_DECISION_MAKER", "CALL_AGAIN"]


def test_a_number_that_never_answers_stops_owning_the_top(db):
    lead = _lead(db)
    for _ in range(osr.MAX_BOOSTED_UNANSWERED):
        osr.apply_call_outcome(db, lead, "NO_ANSWER", {})
    _make_due(db, lead.id)
    (r,) = osr.rank_calls([lead], {}, pending=osr.pending_calls(db))
    assert r["callback_due"] is False, "still queued, no longer boosted"
    assert len(_pending(db, lead.id)) == 1, "nothing is dropped"


def test_a_gatekeeper_is_progress_not_an_unanswered_attempt(db):
    lead = _lead(db)
    for _ in range(osr.MAX_BOOSTED_UNANSWERED):
        osr.apply_call_outcome(db, lead, "NO_ANSWER", {})
    osr.apply_call_outcome(db, lead, "GATEKEEPER", {"decision_maker": "Mr Singh"})
    _make_due(db, lead.id)
    (r,) = osr.rank_calls([lead], {}, pending=osr.pending_calls(db))
    assert r["callback_due"] is True


def test_power_hour_includes_a_due_promise_to_a_lead_with_email(db, monkeypatch):
    """Power Hour used to consider phone-only leads only, so an interested
    buyer who also had a sendable address could never appear in the call
    list -- however overdue the call the founder promised them."""
    from app.api.endpoints import phone_program

    lead = _lead(db, company="Interested, has email")
    osr.apply_call_outcome(db, lead, "INTERESTED", {"decision_maker": "Ms Kaur"})
    _make_due(db, lead.id)
    monkeypatch.setattr(osr, "_email_usable", lambda l: l.id == lead.id)

    out = phone_program(state=None, city=None, limit=15, db=db)
    assert out["power_hour"][0]["lead_id"] == lead.id
    assert out["callbacks_due_today"] == 1
    assert out["phone_only_opportunities"] == 0, "the eligibility count is unchanged"


# ─── the action regenerator no longer erases buyer requests ──────────────────

def test_generate_actions_preserves_call_commitments(db):
    from app.services.crm_tracker import CRMTrackerService

    lead = _lead(db, status="QUALIFIED", estimated_value=500000)
    osr.apply_call_outcome(db, lead, "SAMPLE_REQUESTED", {})
    CRMTrackerService.generate_actions(db)
    rows = _pending(db, lead.id)
    assert [a.action_type for a in rows] == ["SEND_SAMPLE"], (
        "the buyer's sample request must survive a regenerate, and must not "
        "gain a generic reminder on top of it")


def test_generated_types_match_what_the_generator_writes():
    import inspect
    import re
    from app.services import crm_tracker

    src = inspect.getsource(crm_tracker.CRMTrackerService.generate_actions)
    written = set(re.findall(r'action_type\s*=\s*"([A-Z_]+)"', src))
    assert written, "could not read the generator's action types"
    assert written <= set(crm_tracker.GENERATED_ACTION_TYPES), (
        f"generate_actions writes {written - set(crm_tracker.GENERATED_ACTION_TYPES)} "
        f"but would never clear them")


# ─── the scoreboard counts conversations, not dial attempts ──────────────────

def test_call_funnel_does_not_count_a_busy_line_as_a_conversation(db):
    from app.services.phone_intelligence import call_funnel
    lead = _lead(db)
    for key in ("BUSY", "WRONG_PERSON", "NO_ANSWER"):
        osr.apply_call_outcome(db, lead, key, {})
    osr.apply_call_outcome(db, lead, "INTERESTED", {})
    f = {row["stage"]: row["count"] for row in call_funnel(db)["funnel"]}
    assert f["calls made"] == 4
    assert f["conversations"] == 1, "only INTERESTED was a conversation"
