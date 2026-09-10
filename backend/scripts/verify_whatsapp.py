r"""
Ask WhatsApp which of our numbers actually have an account.

A mobile number is not a WhatsApp contact. The network is right; the account
may simply not exist. Treating "it is a mobile" as "it is on WhatsApp" queues
messages that can never arrive — the same failure as putting a landline in
whatsapp_number, one step further along.

The only honest way to know without sending is to ask. Evolution exposes
POST /chat/whatsappNumbers/{instance} for exactly this, and asking costs
nothing. Sending to find out is what damages a number's quality rating, which
is the one thing that would cost the business its WhatsApp presence.

What it writes
--------------
    whatsapp_verified      True  — WhatsApp confirmed an account
                           False — WhatsApp confirmed there is none
                           NULL  — nobody asked, or WhatsApp did not answer
                                   for this number

NULL and False are different facts and are kept apart. Only True makes the
channel eligible; a number WhatsApp declined to answer about stays unasked
rather than being recorded as a negative.

Requires Evolution to be configured and reachable. Unconfigured, it verifies
nothing and says so — it never guesses.

Usage
-----
    python scripts/verify_whatsapp.py                # dry run
    python scripts/verify_whatsapp.py --commit
    python scripts/verify_whatsapp.py --commit --limit 100 --recheck
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BATCH = 50


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--recheck", action="store_true",
                    help="re-ask about numbers already verified")
    args = ap.parse_args()

    from app.observability import enable_utf8_stdout
    enable_utf8_stdout()

    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import lead_journal as journal
    from app.services import whatsapp_evolution as transport

    ok, detail = transport.config_status()
    if not ok:
        print("Cannot verify: " + detail)
        print()
        print("Nothing is recorded when the service is unreachable. A number")
        print("nobody could ask about stays unasked, not marked absent.")
        return 1
    print(f"asking {detail}\n")

    db = SessionLocal()
    checked = confirmed = absent = unanswered = 0
    try:
        q = db.query(B2BLead).filter(B2BLead.whatsapp_number.isnot(None),
                                     B2BLead.whatsapp_number != "")
        if not args.recheck:
            q = q.filter(B2BLead.whatsapp_verified.is_(None))
        leads = q.limit(args.limit).all() if args.limit else q.all()

        if not leads:
            print("No numbers left to ask about.")
            return 0

        for start in range(0, len(leads), BATCH):
            chunk = leads[start:start + BATCH]
            answers = transport.check_numbers([l.whatsapp_number for l in chunk])
            for lead in chunk:
                checked += 1
                key = transport.normalise_msisdn(lead.whatsapp_number)
                if key not in answers:
                    unanswered += 1
                    continue
                exists = answers[key]
                confirmed += 1 if exists else 0
                absent += 0 if exists else 1
                if args.commit:
                    lead.whatsapp_verified = exists
                    lead.whatsapp_verified_at = datetime.utcnow()
                    journal.record(
                        lead, db, method=journal.WHATSAPP,
                        outcome=journal.UPDATED if exists else journal.SKIPPED,
                        remark=("WhatsApp confirmed an account on this number"
                                if exists else
                                "WhatsApp has no account on this number; the "
                                "channel cannot reach this business"),
                        by="verify_whatsapp")
            print(f"  asked about {min(start + BATCH, len(leads))}/{len(leads)}")

        if args.commit:
            db.commit()
    finally:
        db.close()

    print()
    print(f"numbers asked about   : {checked}")
    print(f"   has WhatsApp       : {confirmed}")
    print(f"   no account         : {absent}")
    print(f"   no answer, left NULL: {unanswered}")
    if not args.commit:
        print("\nDRY RUN — nothing written. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
