#!/usr/bin/env python3
"""Cancel queued sends to bounced/suppressed addresses (mark, never delete).

  python cancel_suppressed_queue.py                 # dry run (default): report only
  python cancel_suppressed_queue.py --apply --manifest DIR/cancelled.json

Queued sends are:
  * unconsumed OUTREACH_APPROVED events (same rule as send_queue.pending(),
    7-day TTL) -> settled by inserting APPROVAL_REVOKED (actor BOUNCE_GUARD),
    exactly what send_queue.invalidate_stale() does for unsendable contacts;
  * email_drafts in FOUNDER_APPROVED / APPROVED / QUEUED -> status CANCELLED.
The suppression decision is app/services/bounce_guard.check(), so this script
and the send-time gate can never disagree. Masked output; no email is sent.
"""
import argparse, datetime, json, os, sys

BACKEND = os.environ.get("BG_ROOT", "/opt/purity-revenue-os") + "/purity-revenue-os/backend"
DB = os.path.join(BACKEND, "purity_beans.db")
sys.path.insert(0, os.environ.get("BOUNCE_GUARD_SRC", BACKEND))
from app.services import bounce_guard  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

DRAFT_QUEUED = ("FOUNDER_APPROVED", "APPROVED", "QUEUED")


def mask(a):
    a = (a or "").strip().lower()
    u, _, d = a.partition("@")
    return (u[:1] + "***@" + d) if d else "***"


def pending(conn):
    rows = conn.execute(text(
        "SELECT lead_id, event_type, occurred_at FROM workflow_events WHERE event_type IN "
        "('OUTREACH_APPROVED','EMAIL_SENT','APPROVAL_REVOKED') ORDER BY occurred_at ASC")).fetchall()
    cut = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=7)).isoformat(" ")
    by = {}
    for lid, et, at in rows:
        by.setdefault(lid, []).append((et, str(at or "")))
    out = []
    for lid, r in by.items():
        ap = [a for e, a in r if e == "OUTREACH_APPROVED" and a >= cut]
        st = [a for e, a in r if e != "OUTREACH_APPROVED" and ap and a >= ap[0]]
        if len(ap) > len(st):
            out.append(lid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()
    if a.apply and not a.manifest:
        sys.exit("--apply needs --manifest (rollback record)")

    eng = create_engine(f"sqlite:///{a.db}", connect_args={"timeout": 30})
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    approvals, drafts = [], []
    with eng.connect() as conn:
        for lid in pending(conn):
            row = conn.execute(text("SELECT company, email FROM b2b_leads WHERE id=:i"), {"i": lid}).fetchone()
            if not row:
                continue
            ok, why = bounce_guard.check(row[1], conn)
            if not ok:
                approvals.append({"lead_id": lid, "company": row[0], "email": row[1], "why": why})
        q = conn.execute(text(
            "SELECT d.id, d.lead_id, d.status, coalesce(nullif(trim(d.recipient),''), b.email) "
            "FROM email_drafts d LEFT JOIN b2b_leads b ON b.id=d.lead_id "
            "WHERE d.status IN ('FOUNDER_APPROVED','APPROVED','QUEUED')")).fetchall()
        for did, lid, st, to in q:
            ok, why = bounce_guard.check(to, conn)
            if not ok:
                drafts.append({"draft_id": did, "lead_id": lid, "old_status": st, "email": to, "why": why})

    print(f"queued approvals to bounced/suppressed addresses: {len(approvals)}")
    for x in approvals:
        print(f"  lead {x['lead_id']:>5}  {mask(x['email'])}  {x['why'].replace(x['email'].strip().lower(), mask(x['email']))}")
    print(f"approved/queued drafts to bounced/suppressed addresses: {len(drafts)}")
    for x in drafts:
        print(f"  draft {x['draft_id']:>5} lead {x['lead_id']}  {x['old_status']}  {mask(x['email'])}  {x['why'].replace((x['email'] or '').strip().lower(), mask(x['email']))}")

    if not a.apply:
        print("DRY RUN - nothing changed. Re-run with --apply --manifest <file>.")
        return
    inserted = []
    with eng.begin() as conn:
        for x in approvals:
            payload = {"to": x["email"], "reason": f"BLOCKED: bounce guard - {x['why']}",
                       "note": "queued send cancelled by bounce guard (28 Oct); bounced/suppressed "
                               "addresses are never retried"}
            r = conn.execute(text(
                "INSERT INTO workflow_events (event_type, lead_id, actor, channel, payload, occurred_at) "
                "VALUES ('APPROVAL_REVOKED', :l, 'BOUNCE_GUARD', 'approval', :p, :t)"),
                {"l": x["lead_id"], "p": json.dumps(payload), "t": now.isoformat(" ")})
            inserted.append(r.lastrowid)
        for x in drafts:
            conn.execute(text("UPDATE email_drafts SET status='CANCELLED' WHERE id=:i AND status=:s"),
                         {"i": x["draft_id"], "s": x["old_status"]})
    json.dump({"at_utc": now.isoformat(), "approvals": approvals, "inserted_event_ids": inserted,
               "drafts": drafts}, open(a.manifest, "w"), indent=1)
    print(f"APPLIED: {len(inserted)} approvals revoked, {len(drafts)} drafts CANCELLED; manifest {a.manifest}")


if __name__ == "__main__":
    main()
