#!/usr/bin/env python3
"""
Pre-rotation dry-run: prove the three fail-closed contracts on a temp DB.

No network. No production writes. Exit 0 only if all three pass.

Exit codes:
  0  all pass
 10  missing message_id did not become EMAIL_SENT_UNPROVEN
 11  first proven EMAIL_SENT failed
 12  duplicate message_id did not become EMAIL_SENT_DUPLICATE
 13  decide_after_call failure did not return FOUNDER_REVIEW
 14  ActionQueue missing FOUNDER_REVIEW after engine failure
  1  unexpected error
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch


def main() -> int:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # Must be set BEFORE database module is imported so the engine binds here.
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

    try:
        backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)

        from app.database.database import SessionLocal, engine, Base
        import app.models.models  # noqa: F401
        import app.models.send_proof_fix as spf
        spf.install()
        from app.models.models import B2BLead, WorkflowEvent, ActionQueue
        from app.services.outreach_search import apply_call_outcome

        Base.metadata.create_all(bind=engine)
        db = SessionLocal()

        lead = B2BLead(
            company="DRYRUN Co",
            phone="9876543210",
            status="DISCOVERED",
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)

        # ── A) unproven send ──────────────────────────────────────────
        ev = WorkflowEvent(
            lead_id=lead.id,
            event_type="EMAIL_SENT",
            actor="SYSTEM",
            channel="email",
            payload={"to": "buyer@example.com"},  # no message_id
            occurred_at=datetime.utcnow(),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        if ev.event_type != "EMAIL_SENT_UNPROVEN":
            print(f"FAIL A: expected EMAIL_SENT_UNPROVEN, got {ev.event_type}")
            return 10
        print("PASS A: missing message_id -> EMAIL_SENT_UNPROVEN")

        # ── B) duplicate protection ───────────────────────────────────
        mid = "dryrun-msg-001"
        e1 = WorkflowEvent(
            lead_id=lead.id,
            event_type="EMAIL_SENT",
            actor="SYSTEM",
            channel="email",
            payload={"to": "buyer@example.com", "message_id": mid},
            occurred_at=datetime.utcnow(),
        )
        db.add(e1)
        db.commit()
        db.refresh(e1)
        if e1.event_type != "EMAIL_SENT":
            print(f"FAIL B1: expected EMAIL_SENT, got {e1.event_type}")
            return 11

        e2 = WorkflowEvent(
            lead_id=lead.id,
            event_type="EMAIL_SENT",
            actor="SYSTEM",
            channel="email",
            payload={"to": "buyer@example.com", "message_id": mid},
            occurred_at=datetime.utcnow(),
        )
        db.add(e2)
        db.commit()
        db.refresh(e2)
        if e2.event_type != "EMAIL_SENT_DUPLICATE":
            print(f"FAIL B2: expected EMAIL_SENT_DUPLICATE, got {e2.event_type}")
            return 12
        print("PASS B: duplicate message_id -> EMAIL_SENT_DUPLICATE")

        # ── C) decision engine failure -> FOUNDER_REVIEW ───────────────
        with patch(
            "app.services.phone_intelligence.decide_after_call",
            side_effect=RuntimeError("engine unavailable"),
        ):
            result = apply_call_outcome(db, lead, "NO_ANSWER", captured={})

        if result.get("next_action") != "FOUNDER_REVIEW":
            print(
                f"FAIL C: expected next_action FOUNDER_REVIEW, "
                f"got {result.get('next_action')!r}"
            )
            return 13

        aq = (
            db.query(ActionQueue)
            .filter(
                ActionQueue.lead_id == lead.id,
                ActionQueue.status == "PENDING",
            )
            .all()
        )
        if not any(a.action_type == "FOUNDER_REVIEW" for a in aq):
            print(
                f"FAIL C: ActionQueue missing FOUNDER_REVIEW: "
                f"{[a.action_type for a in aq]}"
            )
            return 14

        print("PASS C: decide_after_call failure -> FOUNDER_REVIEW")
        print("\nALL DRY-RUN GATES PASSED")
        return 0

    except Exception as e:
        print(f"FAIL: unexpected error: {e.__class__.__name__}: {e}")
        return 1
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
