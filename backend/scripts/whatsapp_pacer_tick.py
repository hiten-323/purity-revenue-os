r"""
One WhatsApp pacer tick (cron, Mon-Sat, 09:30-20:00 IST). Sends at most one
allow-listed draft through the single-recipient Klaviyo path.

Prioritizes high-intent leads first:
- Evaluates buying intent signals (intent_score, HOT/HIGH interest level, sample/meeting
  requests, wholesale/distributor division, pricing/margin requests).
- Only leads with verified explicit AI-call consent (AI_CALL_WHATSAPP_REQUEST)
  receive intent priority; all unconsented or unknown leads default to 0.
- Ties broken by consent_timestamp (oldest consent first).

Does nothing unless WHATSAPP_PACER_ENABLED=1.
    python scripts/whatsapp_pacer_tick.py            # one tick
    python scripts/whatsapp_pacer_tick.py --status   # explain, send nothing
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def calculate_lead_intent_score(lead) -> int:
    """Calculate intent score (0-100) for prioritizing WhatsApp outreach.

    Only leads with explicit AI call consent qualify. All other leads return 0.
    """
    if getattr(lead, "consent_source", "") not in ("AI_CALL_WHATSAPP_REQUEST", "EMAIL_REPLY_WHATSAPP_REQUEST"):
        return 0
    if (getattr(lead, "consent_status", "") or "").upper() != "EXPLICIT":
        return 0

    base_score = int(getattr(lead, "intent_score", 0) or 0)
    bonus = 0

    interest = (getattr(lead, "ai_interest_level", "") or "").upper()
    if interest in ("HOT", "HIGH"):
        bonus += 30
    elif interest in ("WARM", "MEDIUM"):
        bonus += 15

    if getattr(lead, "sample_requested", False):
        bonus += 20
    if getattr(lead, "meeting_requested", False):
        bonus += 15

    div = (getattr(lead, "division", "") or "").lower()
    if div in ("distributor", "wholesale", "horeca"):
        bonus += 15

    summary = f"{getattr(lead, 'call_summary', '') or ''} {getattr(lead, 'qualification_notes', '') or ''}".lower()
    if any(k in summary for k in ("price", "rate", "catalogue", "catalog", "margin", "commercial")):
        bonus += 10
    if any(k in summary for k in ("distribute", "stockist", "sample", "backup", "trial")):
        bonus += 10

    return min(100, max(base_score, bonus))


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
    from app.services.outbound_cohort import _cold_outreach_enabled, allow_list_ids
    from app.database.database import SessionLocal
    from app.models.models import B2BLead

    enabled = (os.getenv("WHATSAPP_PACER_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}
    pending = [(did, draft) for did, draft in whatsapp_drafts.next_approved()
               if draft.get("lead_id") != 1 and (_cold_outreach_enabled() or draft.get("lead_id") in allow_list_ids())]

    # Sort pending by intent score (descending), breaking ties by consent_timestamp (oldest first)
    if pending:
        db_sort = SessionLocal()
        try:
            def _sort_key(item):
                did, draft = item
                lead = db_sort.query(B2BLead).filter(B2BLead.id == draft.get("lead_id")).first()
                if not lead:
                    return (0, 0)
                score = calculate_lead_intent_score(lead)
                ts = getattr(lead, "consent_timestamp", None)
                ts_epoch = ts.timestamp() if ts else 0
                return (score, -ts_epoch)
            pending.sort(key=_sort_key, reverse=True)
        except Exception:
            pass
        finally:
            db_sort.close()

    print("pacer_enabled=%s in_hours=%s allow_list=%s approved_on_list=%s hourly=%s daily=%s"
          % (enabled, pacer.in_hours(), sorted(allow_list_ids()), len(pending),
             pacer.max_per_hour(), pacer.max_per_day()))
    if args.status or not enabled or not pacer.in_hours() or not pending:
        return 0

    db = SessionLocal()
    try:
        free, why = pacer.slot_free(db)
        if not free:
            print("no slot:", why); return 0
        for did, draft in pending:
            lead = db.query(B2BLead).filter(B2BLead.id == draft.get("lead_id")).first()
            if lead is None or (not _cold_outreach_enabled() and lead.id not in allow_list_ids()):
                continue
            result = whatsapp_klaviyo.send_one_recipient(
                lead, db, draft_id=str(did),
                template_key=draft.get("template_key") or "",
                params=draft.get("params") or {})
            print("draft %s lead %s -> %s" % (did, lead.id, result.status))
            if result.status in ("queued_to_klaviyo", "failed") and result.message_id:
                from app.models.models import WorkflowEvent
                from datetime import datetime
                db.add(WorkflowEvent(
                    lead_id=lead.id,
                    event_type=("WHATSAPP_QUEUED_TO_KLAVIYO" if result.status == "queued_to_klaviyo"
                                else "WHATSAPP_SEND_UNCONFIRMED"),
                    actor="SYSTEM", channel="whatsapp",
                    payload={"to": pacer.mask(getattr(lead, "consent_phone", "")),
                             "provider": "klaviyo", "draft_id": str(did),
                             "template_key": draft.get("template_key"),
                             "unique_id": result.message_id, "reason": (result.reason or "")[:200]},
                    occurred_at=datetime.utcnow()))
                db.commit()
                whatsapp_drafts.mark_sent(str(did), result.message_id)
            return 0
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
