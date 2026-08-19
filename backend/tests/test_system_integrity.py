"""Optional whole-system verification against a running API + DB.

Not part of the default unit suite. Set SYSTEM_INTEGRITY=1 and ensure the API
is up on :8003 before running as a script or with pytest -k system_integrity.

Does NOT insert jules_session onto sys.path — purity-revenue-os must be the
only app package under test.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest

# This tree's backend only — never jules_session.
_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

pytestmark = pytest.mark.skipif(
    os.getenv("SYSTEM_INTEGRITY", "0").strip() not in ("1", "true", "yes", "on"),
    reason="Live integrity check — set SYSTEM_INTEGRITY=1 and run with API up",
)


def test_system_integrity_live():
    import httpx
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import deliverability as D
    from app.services.email_verifier import verify_email
    from app.services.trust_promoter import may_send as sendable_gate

    B = os.getenv("API_BASE", "http://127.0.0.1:8003/api/v1")
    db = SessionLocal()
    failures = []

    def check(name, ok, detail=""):
        if not ok:
            failures.append(f"{name}: {detail}")

    try:
        sendable = [
            l
            for l in db.query(B2BLead)
            .filter(B2BLead.email != "", B2BLead.email.isnot(None))
            .all()
            if sendable_gate(l)[0]
        ]
        bad = []
        for l in sendable:
            v = verify_email(l.email, l.company or "", l.division or "", l.website or "")
            st = (v.get("status") or "UNVERIFIED").upper()
            if st == "INVALID" or not v.get("mx_valid") or v.get("is_disposable"):
                bad.append(f"{l.email} ({st})")
        check("no conclusive undeliverable sendable", not bad, str(bad[:5]))

        h = D.health(db)
        check("deliverability not self-blocked", h.get("can_send_now") is True, h.get("status_reason", "")[:70])

        from app.services.deliverability import _is_own_refusal

        check(
            "own gate refusal not counted as delivery failure",
            _is_own_refusal({"smtp_error": "BLOCKED: x is on file but not verified"})
            and not _is_own_refusal({"smtp_error": "550 5.4.6 unusual sending activity"}),
        )

        ok_http = True
        for path, params in (
            ("/b2b/outreach/search", {"outreach_method": "PHONE_ONLY"}),
            ("/b2b/leads", {}),
        ):
            try:
                r = httpx.get(B + path, params=params, timeout=30)
                if r.status_code != 200:
                    ok_http = False
            except Exception:
                ok_http = False
        check("core endpoints respond 200", ok_http)
    finally:
        db.close()

    assert not failures, failures
