r"""
The WhatsApp messages the founder should send by hand, highest value first.

This is a draft queue only. Nothing is sent and no lead is mutated unless the
operator explicitly opts into journaling with --journal.

Usage
-----
    python scripts/whatsapp_queue.py
    python scripts/whatsapp_queue.py --limit 10
    python scripts/whatsapp_queue.py --segment distributor
    python scripts/whatsapp_queue.py --csv wa_queue.csv
    python scripts/whatsapp_queue.py --journal      (record QUEUED)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    ap = argparse.ArgumentParser(description="Founder-sent WhatsApp queue (drafts only).")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--segment", default="", help="only this segment, e.g. cafe or distributor")
    ap.add_argument("--csv", default="", help="write the queue to a CSV instead of printing links")
    ap.add_argument("--journal", action="store_true",
                    help="explicitly record a QUEUED step against each lead")
    args = ap.parse_args()

    from app.database.database import SessionLocal
    from app.observability import enable_utf8_stdout, setup_logging
    from app.services.outreach_orchestrator import manual_whatsapp_queue

    enable_utf8_stdout()
    setup_logging("whatsapp_queue")

    db = SessionLocal()
    try:
        want = args.limit * 20 if args.segment else args.limit
        rows = manual_whatsapp_queue(db, limit=want, journal=args.journal)
        if args.segment:
            seg = args.segment.strip().lower()
            rows = [r for r in rows if (r.get("segment") or "").lower() == seg][:args.limit]
    finally:
        db.close()

    if not rows:
        print("Nothing eligible. Either every candidate is suppressed, or no "
              "lead has a WhatsApp-reachable number on record.")
        return 0

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("wrote " + str(len(rows)) + " drafts to " + args.csv)
        return 0

    print("FOUNDER-SENT WHATSAPP -- " + str(len(rows)) + " drafts, nothing sent")
    print("Ranked by coffee_buying_score. Send from your own WhatsApp, then "
          "mark it sent.")
    for i, r in enumerate(rows, 1):
        print("")
        print("-" * 72)
        head = (str(i) + ". " + str(r["company"] or "(no name)")
                + "  [" + str(r.get("segment") or "?") + " / score "
                + str(r.get("coffee_buying_score")) + "]")
        print(head)
        print("   " + str(r.get("city") or "") + "   " + str(r["number"]))
        print("")
        for line in str(r["message"]).split(chr(10)):
            print("   | " + line)
        print("")
        print("   open: " + r["whatsapp_url"])
    print("")
    print("-" * 72)
    print("A reply records EXPLICIT consent. That is the only legitimate way "
          "this database gets WhatsApp opt-in without the AI call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
