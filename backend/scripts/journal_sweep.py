r"""
Give every business its own recorded conclusion.

Autonomous outreach that decides silently is not observable. Anyone looking at
an account should be able to read, from that account, what the system tried,
how, and what it concluded -- without having to grep a log or trust a summary
somebody else produced.

This walks every business through the orchestrator and records the conclusion
on the business itself, in lead_interactions, with:

    method   which channel or subsystem reached the conclusion
    outcome  the conclusion, from a fixed vocabulary so it can be counted
    remark   why, in the words of the gate that decided

Nothing here decides anything new. The orchestrator is the authority; this
reads its verdict and writes it down. It sends nothing, proposes nothing to a
provider, and changes no lead field.

Repeats are dropped
-------------------
lead_journal deduplicates an identical (method, outcome, remark) that follows
the same one, so running this nightly does NOT add 1,858 rows a night. A
business whose situation has not changed gets nothing; the day its answer
changes, the journal shows exactly that day. That is what makes the table a
history rather than a log of polling.

Usage
-----
    python scripts/journal_sweep.py               # dry run: what it would record
    python scripts/journal_sweep.py --commit
    python scripts/journal_sweep.py --commit --limit 50
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def backfill(db, commit: bool) -> dict:
    """Put the outcomes that ALREADY HAPPENED onto the businesses they happened to.

    49 proven EMAIL_SENT events, 56 unproven, 199 failures and 1 WHATSAPP_SENT
    are sitting in workflow_events. They are real: they carry the recipient,
    the message-id and the SMTP error the server actually returned. But
    workflow_events is keyed by the engine's activity, so opening a business
    showed none of it -- 22 companies had genuinely been emailed and their own
    records said nothing had ever happened.

    Nothing here is invented. Every remark is assembled from fields the event
    already holds, and the entry is dated to when the event occurred, not to
    now. An event whose payload will not parse is counted and skipped rather
    than given a plausible-looking summary.
    """
    import json
    from datetime import datetime

    from app.models.models import LeadInteraction, WorkflowEvent
    from app.services import lead_journal as journal

    KINDS = {
        "EMAIL_SENT": (journal.EMAIL, journal.SENT),
        "EMAIL_SENT_UNPROVEN": (journal.EMAIL, journal.SENT),
        "EMAIL_FAILED": (journal.EMAIL, journal.BLOCKED),
        "WHATSAPP_SENT": (journal.WHATSAPP, journal.SENT),
    }

    existing = {(r.lead_id, r.occurred_at, r.method)
                for r in db.query(LeadInteraction).all()}

    stats = {"events": 0, "written": 0, "unparsable": 0, "already_present": 0}
    events = (db.query(WorkflowEvent)
                .filter(WorkflowEvent.event_type.in_(tuple(KINDS)),
                        WorkflowEvent.lead_id.isnot(None))
                .order_by(WorkflowEvent.occurred_at.asc()).all())

    for e in events:
        stats["events"] += 1
        method, outcome = KINDS[e.event_type]
        payload = e.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                stats["unparsable"] += 1
                continue
        if not isinstance(payload, dict):
            stats["unparsable"] += 1
            continue

        # Built only from what the event actually recorded.
        parts = []
        if payload.get("subject"):
            parts.append(f'subject "{payload["subject"]}"')
        if payload.get("to"):
            parts.append(f'to {payload["to"]}')
        if payload.get("message_id"):
            parts.append(f'message-id {payload["message_id"][:60]}')
        if payload.get("smtp_error"):
            parts.append(f'server said: {payload["smtp_error"]}')
        if payload.get("unproven_because"):
            parts.append(f'delivery NOT provable ({payload["unproven_because"]})')
        if str(payload.get("approved_by_founder", "")).lower() == "true":
            parts.append("founder-approved")
        remark = f"{e.event_type}: " + "; ".join(parts) if parts else e.event_type

        key = (e.lead_id, e.occurred_at, method)
        if key in existing:
            stats["already_present"] += 1
            continue

        if commit:
            row = LeadInteraction(
                lead_id=e.lead_id,
                occurred_at=e.occurred_at or datetime.utcnow(),
                created_at=datetime.utcnow(),
                created_by="backfill:workflow_events",
                method=method, outcome=outcome, remark=remark[:1000])
            db.add(row)
        existing.add(key)
        stats["written"] += 1

    if commit:
        db.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the per-decision log lines")
    ap.add_argument("--backfill", action="store_true",
                    help="also copy real historic sends/failures onto the leads")
    args = ap.parse_args()

    from app.observability import enable_utf8_stdout, setup_logging
    enable_utf8_stdout()
    if not args.quiet:
        setup_logging("sweep")

    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import outreach_orchestrator as o

    db = SessionLocal()
    verdicts = Counter()
    would_record = 0
    examined = 0

    try:
        if args.backfill:
            b = backfill(db, args.commit)
            verb = "written" if args.commit else "would write"
            print(f"backfill of real historic outcomes:")
            print(f"   send/failure events found : {b['events']}")
            print(f"   entries {verb:<17}: {b['written']}")
            print(f"   already on the lead       : {b['already_present']}")
            if b["unparsable"]:
                print(f"   unreadable payloads       : {b['unparsable']}"
                      f"  (skipped, never summarised from a guess)")
            print()

        q = db.query(B2BLead).order_by(B2BLead.id)
        leads = q.limit(args.limit).all() if args.limit else q.all()

        for lead in leads:
            examined += 1
            before = len(db.new)
            result = o.next_touch(lead, db)
            verdicts[result["action"]] += 1
            if len(db.new) > before:
                would_record += 1

            if not args.commit:
                # Discard the journal rows the orchestrator staged. Nothing
                # else in next_touch() mutates a lead, so expunging the new
                # objects leaves the database exactly as it was found.
                db.expunge_all()

        if args.commit:
            db.commit()
    finally:
        db.close()

    print()
    print(f"businesses examined   : {examined}")
    for action, n in verdicts.most_common():
        print(f"   {action:<16}{n}")
    print()
    verb = "recorded" if args.commit else "would record"
    print(f"journal entries {verb}: {would_record}")
    print(f"already on file, skipped as unchanged: {examined - would_record}")
    if not args.commit:
        print()
        print("DRY RUN — nothing written. Re-run with --commit.")
    else:
        print()
        print("Read one back with:  python scripts/lead_history.py <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
