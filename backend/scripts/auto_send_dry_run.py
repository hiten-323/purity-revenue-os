r"""Dry run of the auto-send path: what WOULD be approved/sent, nothing written.
    python scripts/auto_send_dry_run.py
Numbers are never printed."""
import os, sys, json
from collections import Counter
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
from app.database.database import SessionLocal
from app.models.models import EmailDraft
from app.services import auto_send as a, human_pace, whatsapp_klaviyo
from app.services import whatsapp_pacer as p
db = SessionLocal()
try:
    e = a.evaluate_email(db, dry_run=True)
    w = a.evaluate_whatsapp(db, dry_run=True)
    from app.models.models import B2BLead
    consented = db.query(B2BLead).filter(B2BLead.consent_status == "EXPLICIT",
                                         B2BLead.consent_source == "AI_CALL_WHATSAPP_REQUEST").all()
    wa_elig = [(l.id, l.company) for l in consented if p.eligible(db, l)[0] and l.id not in a.hold_ids()]
    wa_inel = Counter(p.eligible(db, l)[1] if p.eligible(db, l)[0] is False else "hold list"
                      for l in consented if not (p.eligible(db, l)[0] and l.id not in a.hold_ids()))
    cap = int(os.getenv("EMAIL_DAILY_CAP", "15"))
    out = {
        "flag_OUTREACH_AUTO_SEND": a.enabled(), "window_open_now": e["window_open"],
        "email_sent_today": human_pace.daily_count(), "email_cap": cap, "room_now": e["room"],
        "email_would_approve": len(e["would_approve"]), "email_held_markers": len(e["held_markers"]),
        "email_blocked_content": Counter(w2 for _, w2 in e["blocked_content"]),
        "email_blocked_lead": Counter(w2.split(":")[0][:60] for _, w2 in e["blocked_lead"]),
        "email_skipped_dup_or_already_approved": e["skipped_dup_lead"],
        "wa_config": whatsapp_klaviyo.config_status(), "wa_drafts_would_approve": len(w["would_approve"]),
        "wa_drafts_blocked": [x[1] for x in w["blocked"]],
        "wa_consented_eligible_leads": wa_elig, "wa_consented_not_eligible": wa_inel,
        "draft_status_counts": dict(Counter(s for (s,) in db.query(EmailDraft.status))),
        "hold_ids": sorted(a.hold_ids()),
    }
    print(json.dumps(out, indent=1, default=str))
finally:
    db.close()
