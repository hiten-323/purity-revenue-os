#!/usr/bin/env python3
"""Restore an orphaned lead FK as a non-contactable identity-recovery placeholder.

No phone, email, company identity or consent is fabricated. The placeholder is
DNC + quarantined until the founder supplies verified identity evidence.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "purity_beans.db"
LEAD_ID = 2305

def run(db):
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    result = db.execute("SELECT id,lead_id,outcome,raw_outcome,summary,created_at FROM call_results WHERE id=2707").fetchone()
    if not result or result[1] != LEAD_ID:
        raise RuntimeError("Expected source call_result 2707 / lead 2305 not found")
    if db.execute("SELECT 1 FROM b2b_leads WHERE id=?", (LEAD_ID,)).fetchone():
        return {"created": False, "reason": "lead already exists"}
    company = "[RECOVERY HOLD] Unknown business — orphan lead #2305"
    notes = ("QUARANTINED PLACEHOLDER ONLY. Business identity, phone, email and city were not recovered. "
             "Source call_result=2707; outcome=CATALOGUE_REQUESTED; raw_outcome=WHATSAPP_OPT_IN; "
             "summary=" + str(result[4] or "") + ". Do not contact or send until identity and consent are re-verified.")
    db.execute("""INSERT INTO b2b_leads(
        id,company,company_normalized,lead_source,segment,status,priority,
        qualification_notes,call_status,call_summary,call_outcome_last,
        sample_requested,do_not_call,dnc_reason,consent_status,
        contact_status,contact_status_reason,phone_trust,email_trust,
        business_trust,intelligence_status,contact_confidence,last_updated,
        next_followup_date
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
    (LEAD_ID,company,"orphan-recovery-2305","ORPHAN_RECOVERY_PLACEHOLDER","corporate",
     "QUARANTINED","LOW",notes,"COMPLETED",result[4],"CATALOGUE_REQUESTED",1,1,
     "ORPHAN_IDENTITY_MISSING","UNKNOWN","QUARANTINED",
     "Missing source identity; do not contact","UNKNOWN","UNKNOWN","UNVERIFIED",
     "QUARANTINED",0,now,None))
    db.execute("""INSERT INTO lead_timeline_events(lead_id,event_type,event_date,title,description,meta_json)
        VALUES(?,?,?,?,?,?)""", (LEAD_ID,"ORPHAN_IDENTITY_QUARANTINED",now,
        "Orphan lead identity quarantined",
        "A placeholder restores references only. No company or contact details were invented.",
        json.dumps({"source_call_result_id":2707,"source_call_id":"lead2305-a1-WHATSAPP_OPT_IN-20261102065325",
                    "identity_recovered":False,"contact_blocked":True})))
    if not db.execute("SELECT 1 FROM workflow_events WHERE lead_id=? AND event_type='ORPHAN_IDENTITY_QUARANTINED'",(LEAD_ID,)).fetchone():
        db.execute("""INSERT INTO workflow_events(event_type,lead_id,actor,channel,payload,occurred_at)
            VALUES(?,?,?,?,?,?)""",("ORPHAN_IDENTITY_QUARANTINED",LEAD_ID,"MIGRATION","system",
            json.dumps({"source_call_result_id":2707,"contact_blocked":True,"identity_recovered":False}),now))
    if not db.execute("SELECT 1 FROM outreach_reminders WHERE lead_id=? AND channel='founder_call' AND status='SCHEDULED' AND reason LIKE 'Identity recovery hold:%'",(LEAD_ID,)).fetchone():
        db.execute("""INSERT INTO outreach_reminders(lead_id,channel,sequence_step,due_at,reason,status,created_at)
            VALUES(?,?,?,?,?,?,?)""",(LEAD_ID,"founder_call",4,now,
            "Identity recovery hold: recover verified company/contact identity from original call source; do not contact",
            "SCHEDULED",now))
    db.execute("UPDATE reaudit_orphan_archive SET action='placeholder_created_identity_still_missing' WHERE src_table='call_results' AND src_id=2707")
    db.commit()
    return {"created":True,"lead_id":LEAD_ID,"dnc":True,"contact_status":"QUARANTINED","phone":None,"email":None}

if __name__ == "__main__":
    con=sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=ON")
    print(run(con))
    print("DNC_AUDIT",con.execute("SELECT event_type,source,baseline FROM dnc_audit_events WHERE lead_id=? ORDER BY id DESC LIMIT 2",(LEAD_ID,)).fetchall())
    print("QUICK_CHECK",con.execute("PRAGMA quick_check").fetchone()[0])
    con.close()
