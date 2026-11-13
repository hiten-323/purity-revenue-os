"""4 Nov 2026 MX/trust fix (Hiten approved). Offline: DNS is faked, nothing is sent."""
import types
import pytest
import dns.resolver

from app.services import email_sender as es
from app.services import trust_promoter as tp


class _MX:
    def __init__(self, host):
        self.exchange = host


def _fake(monkeypatch, answer=None, exc=None):
    class R:
        def __init__(self, configure=False):
            self.nameservers = []
        def resolve(self, domain, rtype):
            assert rtype == "MX", "only MX may be consulted (an A record is not enough)"
            if exc:
                raise exc
            return [_MX(h) for h in answer]
    monkeypatch.setattr(dns.resolver, "Resolver", R)
    es._DOMAIN_CHECK_STATE["unavailable"] = False


def test_real_mx_passes(monkeypatch):
    _fake(monkeypatch, ["mx.zoho.in."])
    assert es.domain_is_deliverable("owner@example.in") is True


def test_a_record_only_is_refused(monkeypatch):
    _fake(monkeypatch, exc=dns.resolver.NoAnswer())
    assert es.domain_is_deliverable("info@webonly.com") is False
    assert not es._DOMAIN_CHECK_STATE["unavailable"]
    assert "no MX" in es._DOMAIN_CHECK_STATE["reason"]


def test_nxdomain_is_refused(monkeypatch):
    _fake(monkeypatch, exc=dns.resolver.NXDOMAIN())
    assert es.domain_is_deliverable("a@nope.invalid") is False
    assert not es._DOMAIN_CHECK_STATE["unavailable"]


def test_null_mx_is_refused(monkeypatch):
    _fake(monkeypatch, ["."])
    assert es.domain_is_deliverable("a@nullmx.com") is False


def test_mx_pointing_at_bare_domain_is_refused(monkeypatch):
    _fake(monkeypatch, ["ssktraders.in."])
    assert es.domain_is_deliverable("info@ssktraders.in") is False


def test_dns_outage_defers_not_passes(monkeypatch):
    _fake(monkeypatch, exc=dns.resolver.LifetimeTimeout() if hasattr(dns.resolver, "LifetimeTimeout") else RuntimeError("t"))
    assert es.domain_is_deliverable("a@example.in") is False
    assert es._DOMAIN_CHECK_STATE["unavailable"] is True


def _lead(**kw):
    base = dict(id=1, email="a@example.in", email_trust="VERIFIED", email_source="WEBSITE",
                email_confidence=80, email_verification_status="VALID", email_mx_valid=True,
                email_approved_by_founder=False, company="X", website="")
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.fixture
def no_supplier(monkeypatch):
    import app.services.supplier_guard as sg
    monkeypatch.setattr(sg, "is_supplier", lambda lead: (False, ""))


def test_may_send_refuses_invalid_verdict(no_supplier):
    ok, why = tp.may_send(_lead(email_verification_status="INVALID"))
    assert ok is False and "INVALID" in why


def test_may_send_refuses_no_mx(no_supplier):
    ok, why = tp.may_send(_lead(email_mx_valid=False, email_verification_status="CATCH_ALL"))
    assert ok is False and "MX" in why


def test_unverified_row_not_blocked_by_default_mx_flag(no_supplier):
    # mx_valid=0 is the column default before any check; the live MX gate in send_email covers it
    assert tp.may_send(_lead(email_mx_valid=False, email_verification_status="UNVERIFIED"))[0] is True


def test_may_send_still_allows_valid(no_supplier):
    assert tp.may_send(_lead())[0] is True


def test_send_acceptance_does_not_promote(monkeypatch):
    moved = []
    monkeypatch.setattr(tp, "transition", lambda *a, **k: moved.append(a) or True)
    monkeypatch.setattr(tp, "set_confidence", lambda *a, **k: None)
    lead = _lead(email_trust="VALIDATED", email_source=None)
    assert tp.on_delivery(lead, db=None, delivered=True) == "VALIDATED"
    assert moved == [] and lead.email_source is None


def test_hard_bounce_still_demotes(monkeypatch):
    moved = []
    monkeypatch.setattr(tp, "transition", lambda lead, to, *a, **k: moved.append(to) or True)
    monkeypatch.setattr(tp, "set_confidence", lambda *a, **k: None)
    tp.on_delivery(_lead(), db=None, delivered=False, code="550")
    assert moved == ["BOUNCED"]


def test_contact_trust_source_never_says_delivered():
    import inspect
    from app.services import contact_trust as ct
    src = inspect.getsource(ct.sweep)
    assert 'email_source or "DELIVERED"' not in src
    assert 'event_type == "EMAIL_SENT"' not in src
