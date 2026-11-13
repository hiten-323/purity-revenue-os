#!/usr/bin/env python3
"""One-off data fix for the 4 Nov 2026 MX/trust fix (Hiten approved). Never deletes.

  --dry-run                 report only (default)
  --apply  --manifest F     apply and write the undo record F (JSON)
  --undo   --manifest F     restore exactly what F recorded (only rows still as we left them)

1) Leads whose email_source = 'DELIVERED' (stamped by a send, not a reply) and no reply event:
     VERIFIED/TRUSTED/ACTIVE -> email_trust VALIDATED, email_verified 0, email_source NULL
     BOUNCED/PURGED (terminal)  -> email_source NULL only (trust kept)
   A TRUST_TRANSITION workflow_event is written per demotion; the fingerprint is re-stamped.
2) The 7 undeliverable queued drafts (5644 6133 6175 6184 7197 5380 5410) -> status CANCELLED
   (only if still PENDING/DRAFT/APPROVED/QUEUED/AUTO_APPROVED/FOUNDER_APPROVED).
No email is sent.
"""
import argparse, json, os, sys
from datetime import datetime

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass
import app.models.models  # noqa
from app.models.models import B2BLead, WorkflowEvent, EmailDraft
from app.database.database import SessionLocal
from app.services.contact_trust import stamp

DRAFTS = (5644, 6133, 6175, 6184, 7197, 5380, 5410)
QUEUED = ("PENDING", "DRAFT", "APPROVED", "QUEUED", "AUTO_APPROVED", "FOUNDER_APPROVED")
REPLY = ("EMAIL_REPLIED", "EMAIL_REPLY_RECEIVED")
TERMINAL = ("BOUNCED", "PURGED")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--manifest", default="")
    a = ap.parse_args()
    if (a.apply or a.undo) and not a.manifest:
        sys.exit("--apply/--undo need --manifest")
    db = SessionLocal()
    now = datetime.utcnow()
    if a.undo:
        m = json.load(open(a.manifest))
        n = r = 0
        for x in m["leads"]:
            l = db.get(B2BLead, x["lead_id"])
            if not l or (l.email_trust, l.email_source) != (x["after"]["email_trust"], x["after"]["email_source"]):
                print(f"  skip lead {x['lead_id']}: changed since fix"); continue
            l.email_trust = x["before"]["email_trust"]; l.email_verified = x["before"]["email_verified"]
            l.email_source = x["before"]["email_source"]; stamp(l); n += 1
        for x in m["drafts"]:
            d = db.get(EmailDraft, x["id"])
            if d and d.status == "CANCELLED":
                d.status = x["before"]; r += 1
        db.commit(); print(f"UNDONE: {n} leads, {r} drafts"); return

    rows = db.query(B2BLead).filter(B2BLead.email_source == "DELIVERED").all()
    leads, kept = [], 0
    for l in rows:
        if db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == l.id,
                                          WorkflowEvent.event_type.in_(REPLY)).first():
            kept += 1; continue
        before = {"email_trust": l.email_trust, "email_verified": bool(l.email_verified),
                  "email_source": l.email_source}
        t = (l.email_trust or "").upper()
        after = {"email_trust": l.email_trust if t in TERMINAL else "VALIDATED",
                 "email_verified": bool(l.email_verified) if t in TERMINAL else False,
                 "email_source": None}
        leads.append({"lead_id": l.id, "before": before, "after": after})
        if a.apply:
            l.email_trust = after["email_trust"]; l.email_verified = after["email_verified"]
            l.email_source = None
            if t not in TERMINAL and t != "VALIDATED":
                db.add(WorkflowEvent(lead_id=l.id, event_type="TRUST_TRANSITION", actor="TRUST_ENGINE",
                                     channel="trust", occurred_at=now,
                                     payload={"from": t, "to": "VALIDATED", "source": "",
                                              "reason": "4 Nov 2026 MX/trust fix: a send is not proof of "
                                                        "delivery; 'DELIVERED' provenance removed",
                                              "evidence": ["email_source was DELIVERED, no reply event"],
                                              "address": l.email}))
            stamp(l)
    drafts = []
    for d in db.query(EmailDraft).filter(EmailDraft.id.in_(DRAFTS)).all():
        if (d.status or "").upper() in QUEUED:
            drafts.append({"id": d.id, "lead_id": d.lead_id, "before": d.status})
            if a.apply:
                d.status = "CANCELLED"
                d.reason = ((d.reason or "") + " | CANCELLED 4 Nov 2026: undeliverable address "
                            "(bounce fix, Hiten approved)").strip(" |")
        else:
            print(f"  draft {d.id}: status {d.status}, left alone")
    from collections import Counter
    c = Counter(x["before"]["email_trust"] for x in leads)
    print(f"DELIVERED-stamped leads: {len(rows)}; with a reply (kept): {kept}; to fix: {len(leads)} {dict(c)}")
    print(f"drafts to cancel: {[x['id'] for x in drafts]}")
    if a.apply:
        json.dump({"at_utc": now.isoformat(), "leads": leads, "drafts": drafts}, open(a.manifest, "w"), indent=1)
        db.commit(); print(f"APPLIED; undo record {a.manifest}")
    else:
        db.rollback(); print("DRY RUN - nothing changed")
    db.close()


if __name__ == "__main__":
    main()
