r"""
How many businesses can this engine actually reach, and what is stopping it.

Every conversion rate needs a denominator. "1,858 leads" is not one -- a lead
with no address, no opt-in and no DND scrub is not a prospect, it is a row.
This prints the real denominator per channel, and names the blocker for the
rest, so the next piece of work is chosen by size rather than by intuition.

Read-only. It sends nothing, proposes nothing, and changes no row.

Usage
-----
    python scripts/reachability.py
    python scripts/reachability.py --lead 42     # one business, in detail
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def one(db, lead_id: int) -> int:
    from app.models.models import B2BLead
    from app.services import outreach_orchestrator as o

    lead = db.query(B2BLead).filter(B2BLead.id == lead_id).first()
    if not lead:
        print(f"no business with id {lead_id}")
        return 1

    print(f"{lead.company}  (id {lead.id})")
    print(f"  segment {lead.segment or '-'} / division {lead.division or '-'}   {lead.city or '-'}")
    print()
    stop = o.stop_reason(lead, db)
    if stop:
        print(f"  STOPPED: {stop}")
        print()

    for ch, v in o.eligibility(lead, db).items():
        mark = "YES" if v["eligible"] else "no "
        print(f"  {mark}  {ch:<9} {v['reason']}")
    print()
    nxt = o.next_touch(lead, db)
    print(f"  next: {nxt['action']}"
          + (f" via {nxt['channel']} ({nxt.get('angle', '')})" if nxt.get("channel") else ""))
    print(f"        {nxt['reason']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", type=int, help="report on one business")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from app.database.database import SessionLocal
    from app.services import outreach_orchestrator as o

    db = SessionLocal()
    try:
        if args.lead:
            return one(db, args.lead)
        r = o.reachability_report(db, limit=args.limit)
    finally:
        db.close()

    total = r["leads"]
    reach = r["reachable_on_at_least_one_channel"]
    pct = (reach / total * 100) if total else 0.0

    print(f"REACHABILITY — {total} businesses on record")
    print(f"  suppressed or already engaged : {r['suppressed_or_replied']}")
    print(f"  reachable on >= 1 channel     : {reach}  ({pct:.1f}%)")
    print()
    for ch in o.CHANNELS:
        print(f"  {ch:<9} reachable {r['by_channel'].get(ch, 0)}")
        for reason, n in r["top_blockers"][ch]:
            print(f"            {n:>6}  {reason}")
        print()
    print("Every number above is a count of rows, not an estimate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
