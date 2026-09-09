r"""
Who the founder should call next, and what the AI already learned.

The operating model ends here:

    ONE disclosed AI call  ->  interested?  ->  THIS  ->  founder calls

Without this, founder_call_pipeline.founder_queue() had no caller and an
interested prospect sat in the database with nobody looking at them. A
qualification step whose output nobody reads is a wasted phone call.

Read-only. It advances no stage, sends nothing, and dials nothing. Moving a
lead to FOUNDER_CALL_COMPLETED is the founder's own act, recorded through the
call sheet import or the pipeline directly -- not a side effect of listing.

Nothing is invented. A field the AI never captured prints as "not asked",
never as an empty-looking default: "no current supplier" and "we never asked
who supplies them" are different facts and the founder needs to know which
one they are looking at.

Usage
-----
    python scripts/founder_queue.py
    python scripts/founder_queue.py --limit 10
    python scripts/founder_queue.py --csv queue.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

FIELDS = [
    ("business", "Business"),
    ("segment", "Segment"),
    ("city", "City"),
    ("contact", "Spoke with"),
    ("phone", "Phone"),
    ("interest_level", "Interest"),
    ("currently_uses", "Currently uses"),
    ("requested_callback", "Asked to be called"),
    ("objection", "Objection raised"),
    ("ai_call_summary", "What the AI heard"),
    ("stage", "Stage"),
    ("ai_called_at", "AI called at"),
]


def _show(value) -> str:
    return "not asked" if value in (None, "") else str(value)


def main() -> int:
    ap = argparse.ArgumentParser(description="List leads awaiting a founder call.")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--csv", help="also write the queue to this file")
    args = ap.parse_args()

    from app.observability import enable_utf8_stdout
    enable_utf8_stdout()
    from app.database.database import SessionLocal
    from app.services import founder_call_pipeline as pipeline

    db = SessionLocal()
    try:
        leads = pipeline.founder_queue(db, limit=args.limit)
        briefs = [pipeline.founder_brief(l) for l in leads]
    finally:
        db.close()

    if not briefs:
        print("Nobody is waiting for a founder call.")
        print()
        print("That is the expected state until the AI qualification call has")
        print("run. A lead reaches this queue only by saying yes on it:")
        print("    ELIGIBLE -> AI_CALL_ATTEMPTED -> AI_INTEREST_DETECTED")
        print("               -> FOUNDER_CALL_REQUESTED")
        return 0

    print(f"{len(briefs)} business(es) said yes to the AI and are waiting.\n")
    for i, b in enumerate(briefs, 1):
        print(f"{i}. {_show(b['business'])}  [{_show(b['interest_level'])}]")
        for key, label in FIELDS:
            if key in ("business", "interest_level"):
                continue
            print(f"     {label:<20}{_show(b[key])}")
        print()

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([label for _, label in FIELDS])
            for b in briefs:
                w.writerow([_show(b[key]) for key, _ in FIELDS])
        print(f"written: {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
