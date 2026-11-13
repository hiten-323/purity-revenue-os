"""27 Oct 2026 (Hiten via architect): import-time phone check on b2b_leads.

Dead +91 6060/6106/6144/6968 -> phone_trust DEAD_SERIES; toll-free/premium/
malformed -> INVALID; landlines and mobiles stay valid; do_not_call is never
set (email keeps working); a flagged number cannot reach the call queue.
"""
import pathlib
import pytest

from app.services import phone_intake
from app.services.phone_intake import classify

BACKEND = pathlib.Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("phone", ["+91-61069-44413", "+916060616262", "6144454843",
                                   "+91 69684 48457", "06968448457"])
def test_dead_series_is_flagged_dead_series(phone):
    assert classify(phone)[0] == "DEAD_SERIES"


@pytest.mark.parametrize("phone", ["+91 1800 891 0001", "18001802468", "1860 266 0333",
                                   "+91 1900 123 4567"])
def test_toll_free_and_premium_are_invalid(phone):
    trust, why = classify(phone)
    assert trust == "INVALID" and "toll-free" in why


@pytest.mark.parametrize("phone", ["12345", "+91 98765", "0000000000", "+923361114535",
                                   "+91-98765-432101", "abc"])
def test_malformed_or_foreign_is_invalid(phone):
    assert classify(phone)[0] == "INVALID"


@pytest.mark.parametrize("phone", ["", None, "+91-98765-43210", "9876543210", "+91 70878 70879",
                                   "0172-2271499", "+911722271499", "011 2345 6789", "+91-86880-77000"])
def test_valid_mobiles_landlines_and_empty_pass(phone):
    assert classify(phone) is None


def _session():
    from sqlalchemy.orm import sessionmaker
    from app.database.database import Base
    from app.models.models import B2BLead
    from conftest import memory_engine
    eng = memory_engine()
    Base.metadata.create_all(eng)
    phone_intake.register(B2BLead)
    return sessionmaker(bind=eng)(), B2BLead


def test_insert_flags_before_queue_even_if_caller_sets_discovered():
    db, B2BLead = _session()
    dead = B2BLead(company="Pards Mart", phone="+91-61069-44413", phone_trust="DISCOVERED")
    free = B2BLead(company="Brand HQ", phone="+91 1800 891 0001")
    bad = B2BLead(company="Typo Co", phone="98765")
    land = B2BLead(company="Desk Line", phone="0172-2271499", phone_trust="DISCOVERED")
    mob = B2BLead(company="Real Mobile", phone="+91-98765-43210", phone_trust="DISCOVERED")
    db.add_all([dead, free, bad, land, mob]); db.commit()
    assert dead.phone_trust == "DEAD_SERIES"
    assert free.phone_trust == "INVALID" and bad.phone_trust == "INVALID"
    assert land.phone_trust == "DISCOVERED" and mob.phone_trust == "DISCOVERED"
    # do_not_call is never set: email and WhatsApp keep working
    assert all(not l.do_not_call for l in (dead, free, bad, land, mob))
    # the number itself is kept
    assert dead.phone == "+91-61069-44413"


def test_update_paths_enrichment_rewrite_and_reset():
    db, B2BLead = _session()
    l = B2BLead(company="Smart Choice", phone="+91-98765-43210", phone_trust="DISCOVERED")
    db.add(l); db.commit()
    l.phone = "+91-60606-16262"                 # enrichment writes a dead number
    l.phone_collected_at = None
    db.commit()
    assert l.phone_trust == "DEAD_SERIES" and not l.do_not_call
    l.phone_trust = "DISCOVERED"                # a later sweep tries to re-bless it
    db.commit()
    assert l.phone_trust == "DEAD_SERIES"
    l.phone = "+91 1800 209 4949"
    db.commit()
    assert l.phone_trust == "INVALID"
    l.phone = "+91-86880-77000"                 # corrected to a real number
    db.commit()
    assert l.phone_trust == "DISCOVERED"


def test_carrier_and_quarantine_evidence_is_never_lifted():
    db, B2BLead = _session()
    a = B2BLead(company="A", phone="+91-98765-43210", phone_trust="SIP_INVALID")
    b = B2BLead(company="B", phone="+91-98765-43211", phone_trust="SHARED_QUARANTINE")
    c = B2BLead(company="C", phone="+91-98765-43212", phone_trust="INVALID")
    db.add_all([a, b, c]); db.commit()
    for x in (a, b, c):
        x.phone = "+91-70878-70879"
    db.commit()
    assert (a.phone_trust, b.phone_trust, c.phone_trust) == ("SIP_INVALID", "SHARED_QUARANTINE", "INVALID")


def test_needs_new_number_lifted_only_by_a_new_dialable_number():
    db, B2BLead = _session()
    l = B2BLead(company="Smart Choice", phone="", phone_trust="NEEDS_NEW_NUMBER")
    db.add(l); db.commit()
    l.city = "Mohali"; db.commit()
    assert l.phone_trust == "NEEDS_NEW_NUMBER"
    l.phone = "+91-61069-44413"; db.commit()      # a dead number is still flagged
    assert l.phone_trust == "DEAD_SERIES"
    l2 = B2BLead(company="Smart Choice 2", phone="", phone_trust="NEEDS_NEW_NUMBER")
    db.add(l2); db.commit()
    l2.phone = "+91-98765-43219"; db.commit()
    assert l2.phone_trust == "DISCOVERED" and not l2.do_not_call


def test_unrelated_update_does_not_touch_trust():
    db, B2BLead = _session()
    l = B2BLead(company="X", phone="+91-98765-43210", phone_trust="DISCOVERED")
    db.add(l); db.commit()
    l.city = "Mohali"; db.commit()
    assert l.phone_trust == "DISCOVERED"


def test_register_is_idempotent():
    from sqlalchemy import event
    from app.models.models import B2BLead
    phone_intake.register(B2BLead); phone_intake.register(B2BLead); phone_intake.register()
    assert event.contains(B2BLead, "before_insert", phone_intake._before_insert)


def test_flagged_states_are_blocked_from_the_call_queue():
    from app.services.founder_call_pipeline import PHONE_TRUST_BLOCKED
    assert {"INVALID", "DEAD_SERIES"} <= PHONE_TRUST_BLOCKED
    src = (BACKEND / "app/services/ai_call_cycle.py").read_text(encoding="utf-8")
    assert '"SIP_INVALID", "DEAD_SERIES"]' in src


def test_models_registers_the_guard_for_every_writer():
    from sqlalchemy import event
    import app.models.models as m
    assert event.contains(m.B2BLead, "before_update", phone_intake._before_update)
    assert "phone_intake" in (BACKEND / "app/models/models.py").read_text(encoding="utf-8")


def test_import_scripts_register_the_guard():
    for s in ("scripts/seed_ncr_distributors.py", "scripts/seed_north_distributors_20261015.py"):
        assert "phone_intake" in (BACKEND / s).read_text(encoding="utf-8"), s


@pytest.mark.parametrize("text", ["facebook.com/106106944413135", "id=103561444548435",
                                  "profile.php?id=100060616262123"])
def test_phone_regex_does_not_cut_numbers_out_of_long_ids(text):
    from app.services.identity import PHONE_RE
    assert PHONE_RE.findall(text) == []


@pytest.mark.parametrize("text,want", [("Call 09876543210", "9876543210"),
                                       ("+91 98765 43210", "+91 98765 43210"),
                                       ("919876543210", "919876543210"),
                                       ("Tel:98765-43210,", "98765-43210")])
def test_phone_regex_still_finds_real_numbers(text, want):
    from app.services.identity import PHONE_RE
    assert PHONE_RE.findall(text) == [want]
