r"""
Sync job (reaudit-fix 29 Oct 2026, Hiten approved "Fix all above").
Read-mostly, idempotent, sends nothing. Every run:
 1) outbound_whatsapp  <- workflow_events WHATSAPP_SENT / WHATSAPP_QUEUED_TO_KLAVIYO /
    WHATSAPP_SEND_UNCONFIRMED (one row per send, keyed on the provider unique id),
    and WHATSAPP_NOT_DELIVERED marks the matching row FAILED. Recipient stored MASKED.
 2) outreach_experiments 'call_opener_ab' (TRACK_ONLY, changes no behaviour) +
    outreach_experiment_assignments <- call_results.opener_variant (first variant per lead),
    with per-variant calls/connected counts in variants_json.
    python scripts/sync_wa_opener_tables.py [--dry-run]
"""
import json, re, sqlite3, sys, os
from datetime import datetime

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "purity_beans.db")
DRY = "--dry-run" in sys.argv
STATUS = {"WHATSAPP_SENT": "SENT", "WHATSAPP_QUEUED_TO_KLAVIYO": "QUEUED_TO_KLAVIYO",
          "WHATSAPP_SEND_UNCONFIRMED": "UNCONFIRMED"}

def mask(p):
    d = re.sub(r"\D", "", str(p or ""))
    return ("XXXXXX" + d[-4:]) if len(d) >= 4 else (p or "")

def main():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    now = datetime.utcnow().isoformat(sep=" ")
    ins = upd = 0
    have = {r[0] for r in c.execute("select whatsapp_message_id from outbound_whatsapp where whatsapp_message_id is not null")}
    rows = c.execute("select id, lead_id, event_type, payload, occurred_at from workflow_events "
                     "where event_type in ('WHATSAPP_SENT','WHATSAPP_QUEUED_TO_KLAVIYO','WHATSAPP_SEND_UNCONFIRMED') order by id").fetchall()
    for r in rows:
        try: p = json.loads(r["payload"] or "{}")
        except Exception: p = {}
        if isinstance(p, str):
            try: p = json.loads(p)
            except Exception: p = {}
        mid = str(p.get("unique_id") or p.get("message_id") or f"wfe-{r['id']}")
        if mid in have: continue
        st = STATUS[r["event_type"]]
        msg = p.get("template_key") or p.get("template") or ""
        resp = json.dumps({"source": "workflow_events", "workflow_event_id": r["id"],
                           "provider": p.get("provider") or "", "draft_id": p.get("draft_id") or "",
                           "reason": (p.get("reason") or "")[:200]})
        if not DRY:
            c.execute("insert into outbound_whatsapp (lead_id, recipient, message, whatsapp_message_id, status, "
                      "whatsapp_response, created_at, queued_at, sent_at, retry_count) values (?,?,?,?,?,?,?,?,?,0)",
                      (r["lead_id"], mask(p.get("to")), msg, mid, st, resp, r["occurred_at"], r["occurred_at"],
                       r["occurred_at"] if st == "SENT" else None))
        have.add(mid); ins += 1
    for r in c.execute("select id, lead_id, payload, occurred_at from workflow_events where event_type='WHATSAPP_NOT_DELIVERED'").fetchall():
        try: p = json.loads(r["payload"] or "{}")
        except Exception: p = {}
        mid = p.get("unique_id")
        if mid and not DRY:
            n = c.execute("update outbound_whatsapp set status='FAILED', failed_at=coalesce(failed_at, ?) "
                          "where whatsapp_message_id=? and status!='FAILED'", (r["occurred_at"], mid)).rowcount
            upd += n
    # opener A/B
    exp = c.execute("select id from outreach_experiments where name='call_opener_ab'").fetchone()
    stats = {v: {"calls": n, "leads": l, "connected": k or 0} for v, n, l, k in c.execute(
        "select opener_variant, count(*), count(distinct lead_id), sum(connected) from call_results "
        "where opener_variant is not null and opener_variant!='' group by 1")}
    vj = json.dumps({"variants": sorted(stats), "stats": stats, "synced_at": now,
                     "note": "track-only; filled from call_results.opener_variant"})
    if not DRY:
        if exp is None:
            c.execute("insert into outreach_experiments (name, hypothesis, status, variants_json, created_at) values (?,?,?,?,?)",
                      ("call_opener_ab", "Which AI-call opener variant gets more connected/interested calls",
                       "TRACK_ONLY", vj, now))
            exp_id = c.execute("select id from outreach_experiments where name='call_opener_ab'").fetchone()[0]
        else:
            exp_id = exp[0]
            c.execute("update outreach_experiments set variants_json=? where id=?", (vj, exp_id))
        a = c.execute("""insert or ignore into outreach_experiment_assignments (experiment_id, lead_id, variant, assigned_at, active)
            select ?, cr.lead_id, cr.opener_variant, coalesce(cr.started_at, cr.created_at), 0 from call_results cr
            join b2b_leads l on l.id=cr.lead_id
            where cr.opener_variant is not null and cr.opener_variant!='' and cr.id = (
              select id from call_results c2 where c2.lead_id=cr.lead_id and c2.opener_variant is not null and c2.opener_variant!=''
              order by coalesce(c2.started_at, c2.created_at), c2.id limit 1)""", (exp_id,)).rowcount
        c.commit()
    else:
        a = "dry"
    print(f"{now} outbound_whatsapp inserted={ins} failed_marked={upd} opener_assignments_added={a} variants={sorted(stats)}")

if __name__ == "__main__":
    main()
