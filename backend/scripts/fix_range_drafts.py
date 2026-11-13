r"""29 Oct 2026 (Hiten 4:08 PM): re-word open drafts written by the old templates
(Bold/Ultra Blend lumped in with 'freeze-dried'; Ultra Blend '(signature,
agglomerated)' with no chicory), then re-run the auto-send gate on them.
Only DRAFT/PENDING/EDITED drafts that the content gate currently blocks are
touched; BLOCKED_* statuses are never read. Dry run unless --apply.
Row backups go to the folder given by --backup (JSON)."""
import os, sys, json, argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)
from app.database.database import SessionLocal
from app.models.models import EmailDraft, B2BLead
from app.services import auto_send as a

OLD_NEW = [
    ("Purity Beans is a premium instant coffee brand of Pure Pantry Provisions. We offer a portfolio of premium "
     "freeze-dried and agglomerated instant coffees, with zero artificial additives; Purica, Purista, Bold and "
     "Prima are pure coffee with no chicory.",
     "Purity Beans is a premium instant coffee brand of Pure Pantry Provisions, with zero artificial additives. "
     "Purica and Purista are freeze-dried. Bold and Ultra Blend are agglomerated. Purica, Purista, Bold and Prima "
     "are pure coffee with no chicory. Ultra Blend is our coffee-chicory blend (it contains chicory)."),
    ("Purity Beans is a premium instant coffee brand of Pure Pantry Provisions. We offer an instant coffee range - "
     "premium freeze-dried and agglomerated options - with zero artificial flavors and no additives. Purica, "
     "Purista, Bold and Prima are pure coffee with no chicory.",
     "Purity Beans is a premium instant coffee brand of Pure Pantry Provisions, with zero artificial flavors and no "
     "additives. Purica and Purista are freeze-dried. Bold and Ultra Blend are agglomerated. Purica, Purista, Bold "
     "and Prima are pure coffee with no chicory. Ultra Blend is our coffee-chicory blend (it contains chicory)."),
    ("Ultra Blend (signature, agglomerated)", "Ultra Blend (coffee-chicory blend, contains chicory, agglomerated)"),
    ("our Ultra Blend and Bold agglomerated coffees offer excellent quality at a highly competitive price point.",
     "our agglomerated coffees, Bold and Ultra Blend, offer excellent quality at a highly competitive price point. "
     "Ultra Blend is our coffee-chicory blend (it contains chicory)."),
    ("our full range (Purica, Purista, Bold, Ultra Blend) is available in lead-free 100 g glass jars.",
     "our full range (Purica, Purista, Bold and Ultra Blend) is available in lead-free 100 g glass jars. "
     "Ultra Blend is our coffee-chicory blend (it contains chicory)."),
]

ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); ap.add_argument("--backup", default="")
args = ap.parse_args()
db = SessionLocal(); out = []; backup = []
try:
    for d in db.query(EmailDraft).filter(EmailDraft.status.in_(a.CANDIDATE_STATUSES)).order_by(EmailDraft.id):
        v0, why0 = a.content_check(d.subject, d.body)
        if v0 != "BLOCK":
            continue
        body = d.body or ""
        for o, n in OLD_NEW:
            body = body.replace(o, n)
        if body == (d.body or ""):
            out.append({"draft": d.id, "lead": d.lead_id, "before": why0, "after": "not a template issue; unchanged"}); continue
        v1, why1 = a.content_check(d.subject, body)
        lead = db.query(B2BLead).filter(B2BLead.id == d.lead_id).first()
        lok, lwhy = a.lead_check(db, lead, d.recipient or "")
        out.append({"draft": d.id, "lead": d.lead_id, "touch": d.follow_up_type, "before": why0,
                    "after_content": f"{v1} {why1}".strip(), "lead_gate": "pass" if lok else lwhy,
                    "eligible_now": v1 == "OK" and lok})
        if args.apply and v1 == "OK":
            backup.append({"id": d.id, "body": d.body, "reason": d.reason})
            d.body = body
            d.reason = ((d.reason or "") + " | 29 Oct: re-worded to fixed range copy (Bold/Ultra agglomerated; Ultra contains chicory)")[:1000]
    if args.apply and backup:
        if args.backup:
            with open(os.path.join(args.backup, "drafts_before_fix.json"), "w") as f:
                json.dump(backup, f, indent=1)
        db.commit()
    print(json.dumps({"applied": bool(args.apply), "changed": len(backup), "rows": out}, indent=1, default=str))
finally:
    db.close()
