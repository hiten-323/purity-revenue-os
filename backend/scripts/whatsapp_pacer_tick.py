r"""
One WhatsApp pacer tick (cron, Mon-Sat, 09:30-19:00 IST). Sends at most one
allow-listed draft through the single-recipient Klaviyo path.

Does nothing unless WHATSAPP_PACER_ENABLED=1. KLAVIYO_WA_FLOW_LIVE is not
read here and must stay 0: this tick does not turn a flow on and does not
walk the rest of the queue. The cron stays commented until an operator
enables the flag.
    python scripts/whatsapp_pacer_tick.py            # one tick
    python scripts/whatsapp_pacer_tick.py --status   # explain, send nothing
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
    except Exception:
        pass
    from app.services import whatsapp_drafts, whatsapp_klaviyo, whatsapp_pacer as pacer
    from app.services.outbound_cohort import allow_list_ids
    enabled = (os.getenv("WHATSAPP_PACER_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    pending = [(did, draft) for did, draft in whatsapp_drafts.next_approved()
               if draft.get("lead_id") in allow_list_ids()]
    print("pacer_enabled=%s in_hours=%s allow_list=%s approved_on_list=%s hourly=%s daily=%s"
          % (enabled, pacer.in_hours(), sorted(allow_list_ids()), len(pending),
             pacer.max_per_hour(), pacer.max_per_day()))
    if args.status or not enabled or not pacer.in_hours() or not pending:
        return 0
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    db = SessionLocal()
    try:
        free, why = pacer.slot_free(db)
        if not free:
            print("no slot:", why); return 0
        for did, draft in pending:
            lead = db.query(B2BLead).filter(B2BLead.id == draft.get("lead_id")).first()
            if lead is None or lead.id not in allow_list_ids():
                continue
            result = whatsapp_klaviyo.send_one_recipient(
                lead, db, draft_id=str(did),
                template_key=draft.get("template_key") or "",
                params=draft.get("params") or {})
            print("draft %s lead %s -> %s" % (did, lead.id, result.status))
            return 0
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
