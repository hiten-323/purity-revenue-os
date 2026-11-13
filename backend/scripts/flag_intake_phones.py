"""Apply the import-time phone rule (app/services/phone_intake.py) to rows that
are already in b2b_leads.

    python scripts/flag_intake_phones.py            # dry run (default): report only
    python scripts/flag_intake_phones.py --apply    # set phone_trust INVALID / DEAD_SERIES

Never sets do_not_call (email keeps working) and never changes a number.
Undo: restore phone_trust from the printed "was" column.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    apply_ = "--apply" in sys.argv
    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.services import phone_intake
    db = SessionLocal()
    n = 0
    try:
        for l in db.query(B2BLead).filter(B2BLead.phone.isnot(None), B2BLead.phone != "").order_by(B2BLead.id):
            res = phone_intake.classify(l.phone)
            if res and (l.phone_trust or "").upper() != res[0]:
                n += 1
                print(f"{l.id}|{l.company}|{l.phone}|was={l.phone_trust}|-> {res[0]}|{res[1]}")
                if apply_:
                    l.phone_trust = res[0]
        if apply_:
            db.commit()
        else:
            db.rollback()
        print(f"{'APPLIED' if apply_ else 'DRY RUN'}: {n} row(s) {'flagged' if apply_ else 'would be flagged'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
