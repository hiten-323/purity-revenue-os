"""15 Oct 2026 - North India instant-coffee distributor seed (web research).
Sources: Nestle Professional public distributor locator (NESCAFE distributors)
and jsco.in (wholesaler web store listing Bru Gold). Contacts are as published.
No email unless self-published; no WhatsApp set. Dry run by default; --commit writes.
Rollback: delete rows where phone_source in ('NESTLE_DISTRIBUTOR_LOCATOR','WEBSITE')
and lead_source='WEB_RESEARCH_20261015'."""
import argparse, json, os, re, sys
from datetime import datetime
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
SRC = r"C:\Users\hiten\Desktop\ppp\new_leads.json"
def d10(p):
    d = re.sub(r"\D", "", str(p or "")); return d[-10:] if len(d) >= 10 else d
def nm(s):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(pvt|private|ltd|limited|m/s|llp|the|co|company)\b", "", (s or "").lower()))
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--commit", action="store_true"); a = ap.parse_args()
    from app.database.database import SessionLocal
    from app.models.models import B2BLead, LeadEvidence
    from app.services.phone_intake import register as _phone_intake; _phone_intake()  # 27 Oct 2026 import-time phone check
    db = SessionLocal(); added = []; skipped = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        leads = db.query(B2BLead).all()
        ph = set(); names = set()
        for l in leads:
            for p in (l.phone, l.whatsapp_number):
                if d10(p): ph.add(d10(p))
            names.add(nm(l.company))
        for r in json.load(open(SRC)):
            k = d10(r["phone"])
            if k in ph: skipped.append(("dupe phone", r["company"])); continue
            if nm(r["company"]) in names: skipped.append(("dupe name", r["company"])); continue
            web = r["src"] if "jsco" in r["src"] else None
            lead = B2BLead(company=r["company"], contact_name=r["contact"] or None, address=r["address"],
                           city=r["city"], state=r["state"], phone=r["phone"],
                           phone_source="WEBSITE" if web else "NESTLE_DISTRIBUTOR_LOCATOR",
                           lead_source="WEB_RESEARCH_20261015", phone_collected_at=datetime.utcnow(),
                           website="https://jsco.in/" if web else None, segment="distributor",
                           division="RETAIL", status="NEW",
                           coffee_buying_evidence=f"[INSTANT_COFFEE_FIT {ts} IST] CONFIRMED | web | {r['src']} | {r['note']}")
            if r.get("email"): lead.email = r["email"]; lead.email_source = "WEBSITE"
            if a.commit:
                db.add(lead); db.flush()
                db.add(LeadEvidence(lead_id=lead.id, signal_type="DISCOVERY_SOURCE", value=r["note"],
                                    source=lead.phone_source, source_url=r["src"],
                                    collected_at=datetime.utcnow(), verified=False))
            added.append((getattr(lead, "id", None), r["company"], r["city"], r["phone"]))
            ph.add(k); names.add(nm(r["company"]))
        if a.commit: db.commit()
    finally:
        db.close()
    for x in added: print("add", x)
    for x in skipped: print("skip", x)
    print(f"added {len(added)} skipped {len(skipped)}", "" if a.commit else "DRY RUN")
if __name__ == "__main__": main()
