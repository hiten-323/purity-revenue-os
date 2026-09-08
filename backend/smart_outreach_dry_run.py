"""Safe, no-network smart-outreach dry run.

Copies the local SQLite database to a temporary file, points SQLAlchemy at the
copy, and replaces execute_one with a planner-only function. No email, WhatsApp,
or provider function is called. The copied DB is deleted on exit.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    backend = Path(__file__).resolve().parent
    source = backend / "purity_beans.db"
    if not source.exists():
        print(f"DRY RUN ABORTED: database not found: {source}")
        return 2

    with tempfile.TemporaryDirectory(prefix="purity-outreach-dryrun-") as tmp:
        copy = Path(tmp) / "purity_beans.db"
        shutil.copy2(source, copy)
        os.environ["DATABASE_URL"] = f"sqlite:///{copy}"
        os.environ["AUTO_OUTREACH_ENABLED"] = "0"
        os.environ["SMART_OUTREACH_ENABLED"] = "0"

        from app.database.database import SessionLocal
        from app.models.models import B2BLead
        from app.services import smart_outreach as so
        from app.services.decision_engine import evaluate_next_action

        db = SessionLocal()
        scanned = 0
        eligible = 0
        rows = []
        try:
            candidates = (
                db.query(B2BLead)
                .filter(B2BLead.contact_status.notin_(["OPTED_OUT", "DO_NOT_CONTACT", "BOUNCED"]))
                .filter(B2BLead.status.notin_(["DO_NOT_CONTACT", "CLOSED_LOST", "DISQUALIFIED", "ORDER_WON"]))
                .filter(
                    ((B2BLead.email.isnot(None)) & (B2BLead.email != ""))
                    | ((B2BLead.whatsapp_number.isnot(None)) & (B2BLead.whatsapp_number != ""))
                )
                .order_by(B2BLead.coffee_buying_score.desc().nullslast(), B2BLead.score.desc(), B2BLead.id.asc())
                .limit(160)
                .all()
            )
            scanned = len(candidates)

            for lead in candidates:
                try:
                    verdict = evaluate_next_action(lead, db)
                except Exception as exc:
                    rows.append({"lead_id": lead.id, "company": lead.company, "status": "GATE_ERROR", "error": type(exc).__name__})
                    continue
                if verdict.get("action") not in so.SEND_ELIGIBLE_ACTIONS:
                    continue
                profile = so.classify_lead(db, lead)
                plan = so.plan_touch(db, lead, profile)
                eligible += 1
                rows.append({
                    "lead_id": lead.id,
                    "company": lead.company,
                    "email": lead.email,
                    "phone": lead.whatsapp_number,
                    "category": profile.category,
                    "warmth": profile.warmth,
                    "intent": profile.intent,
                    "authority_action": verdict.get("action"),
                    "planned_action": plan.get("action"),
                    "channel": plan.get("channel"),
                    "execute": plan.get("execute"),
                    "reason": plan.get("reason"),
                    "status": "WOULD_SEND" if plan.get("execute") else "HELD",
                })

            print("=== PURITY SMART OUTREACH DRY RUN ===")
            print("NO NETWORK / NO EMAIL / NO WHATSAPP / TEMPORARY DB COPY")
            print(f"Database copy: {copy}")
            print(f"Candidates scanned: {scanned}")
            print(f"SEND-eligible by decision engine: {eligible}")
            print(f"Would execute: {sum(1 for r in rows if r.get('status') == 'WOULD_SEND')}")
            print(f"Held/errors: {sum(1 for r in rows if r.get('status') != 'WOULD_SEND')}")
            print("--- PLAN ---")
            print(json.dumps(rows, indent=2, default=str))
            return 0
        finally:
            db.rollback()
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
