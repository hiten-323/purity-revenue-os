r"""
One business's complete story: what we tried, how, and what we concluded.

Every step now writes to lead_interactions -- the table that was already
described as "the permanent, append-only record of every interaction with a
business" but which only founder calls ever used. Email refusals, trust
promotions, AI call outcomes and orchestrator conclusions all land there now,
so a single account's history reads in one place and in the order it happened.

Read-only. Prints what is recorded and nothing else: a field nobody captured
shows as blank rather than as a plausible default.

Usage
-----
    python scripts/lead_history.py 1274
    python scripts/lead_history.py --company "Hyatt"
    python scripts/lead_history.py --summary          # coverage across the estate
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _print_lead(db, lead):
    from app.services import lead_journal as journal

    print(f"{lead.company}   (id {lead.id})")
    bits = [b for b in (lead.segment, lead.division, lead.city) if b]
    print("  " + " / ".join(bits) if bits else "  (no segment recorded)")
    print(f"  email {lead.email or '-'}   trust {lead.email_trust or '-'}"
          f"   confidence {lead.email_confidence if lead.email_confidence is not None else '-'}")
    print(f"  phone {lead.phone or '-'}   whatsapp {lead.whatsapp_number or '-'}"
          f"   consent {lead.consent_status or '-'}")
    print(f"  pipeline stage {lead.outreach_stage or 'ELIGIBLE (nothing attempted)'}")
    print()

    rows = journal.history(db, lead.id)
    if not rows:
        print("  No recorded steps.")
        print("  Nothing has been attempted against this business yet, or every")
        print("  evaluation reached a conclusion already on file — repeats are")
        print("  deduped so the journal stays a history of changes.")
        return

    print(f"  {len(rows)} recorded step(s), oldest first:")
    print()
    for r in rows:
        when = r["at"].strftime("%Y-%m-%d %H:%M") if r["at"] else "(no date)"
        print(f"  {when}  {r['method']:<13} {r['outcome']:<14} [{r['by']}]")
        if r["remark"]:
            for line in _wrap(r["remark"], 74):
                print(f"       {line}")
        for label, key in (("decision maker", "decision_maker"),
                           ("current supplier", "current_supplier"),
                           ("monthly kg", "monthly_kg"),
                           ("next follow-up", "next_followup")):
            if r.get(key) not in (None, "", 0):
                print(f"       {label}: {r[key]}")
        print()


def _wrap(text, width):
    words, line, out = str(text).split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="One business's recorded history.")
    ap.add_argument("lead_id", nargs="?", type=int)
    ap.add_argument("--company", help="match on company name (substring)")
    ap.add_argument("--summary", action="store_true",
                    help="how much of the estate has any history at all")
    args = ap.parse_args()

    from app.observability import enable_utf8_stdout
    enable_utf8_stdout()
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import lead_journal as journal

    db = SessionLocal()
    try:
        if args.summary:
            s = journal.summary(db)
            pct = s["with_history"] / s["businesses"] * 100 if s["businesses"] else 0
            opct = (s["businesses_with_an_outcome"] / s["businesses"] * 100
                    if s["businesses"] else 0)
            print(f"businesses                 : {s['businesses']}")
            print(f"with any recorded step     : {s['with_history']}  ({pct:.1f}%)")
            print(f"recorded steps             : {s['entries']}")
            print()
            # Kept apart deliberately. An assessment is what the system
            # concluded; an outcome is something that happened TO the business.
            # Counting them together lets a system that has contacted nobody
            # report a large number of "steps".
            print(f"REAL OUTCOMES              : {s['real_outcomes']}"
                  f"   across {s['businesses_with_an_outcome']} business(es)"
                  f"  ({opct:.1f}%)")
            print(f"assessments (no contact)   : {s['assessments']}")
            print()
            if s["outcome_breakdown"]:
                print("real outcomes by kind:")
                for k, v in s["outcome_breakdown"].items():
                    print(f"   {k:<20}{v}")
            else:
                print("real outcomes by kind: none — nothing has happened to any")
                print("business yet. Every recorded step is an assessment.")
            print()
            print("by method:")
            for k, v in s["by_method"].items():
                print(f"   {k:<20}{v}")
            return 0

        if args.company:
            leads = (db.query(B2BLead)
                       .filter(B2BLead.company.ilike(f"%{args.company}%"))
                       .limit(5).all())
            if not leads:
                print(f"No business matching {args.company!r}")
                return 1
        elif args.lead_id:
            lead = db.query(B2BLead).filter(B2BLead.id == args.lead_id).first()
            if not lead:
                print(f"No business with id {args.lead_id}")
                return 1
            leads = [lead]
        else:
            ap.print_help()
            return 1

        for i, lead in enumerate(leads):
            if i:
                print("-" * 78)
            _print_lead(db, lead)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
