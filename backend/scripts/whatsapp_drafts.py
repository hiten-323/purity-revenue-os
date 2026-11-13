r"""
WhatsApp drafts for founder approval. Nothing is sent from here.
    python scripts/whatsapp_drafts.py --list
    python scripts/whatsapp_drafts.py --create --template pb_catalogue_after_call   # PENDING drafts for consented leads
    python scripts/whatsapp_drafts.py --approve <draft_id> --by "Hiten (msg 2026-10-29 10:02)"
    python scripts/whatsapp_drafts.py --reject <draft_id>
Phone numbers are masked in all output.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--template", default="")
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--by", default="")
    a = ap.parse_args()
    from app.services import whatsapp_drafts as wd
    from app.services.whatsapp_pacer import mask
    if a.approve:
        print(wd.approve(a.approve, a.by)); return 0
    if a.reject:
        wd.reject(a.reject); print("rejected", a.reject); return 0
    if a.create:
        if not a.template:
            print("--template required (an APPROVED Klaviyo WhatsApp template key)"); return 2
        from app.database.database import SessionLocal
        from app.models.models import B2BLead
        db = SessionLocal()
        try:
            rows = db.query(B2BLead).filter(B2BLead.consent_status == "EXPLICIT",
                                            B2BLead.consent_source == "AI_CALL_WHATSAPP_REQUEST").all()
            for l in rows:
                did = wd.create(l.id, a.template, {"name": (l.contact_name or "").split(" ")[0] or "ji",
                                                   "company": l.company or ""})
                print(did, l.id, l.company, mask(l.consent_phone))
        finally:
            db.close()
        return 0
    for k, v in wd.all_drafts().items():
        print(k, v.get("status"), "lead", v.get("lead_id"), v.get("template_key"), v.get("params"),
              "approved_by:", v.get("approved_by") or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
