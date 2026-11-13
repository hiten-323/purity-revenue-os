"""Mark leads whose phone returned SIP 400 on 2+ separate attempts on 23-24 Oct 2026
(and never rang/connected later) as do_not_call. Single-400 numbers are NOT purged.

Usage (from backend/):
    python scripts/purge_invalid_numbers.py /path/to/purge_400_numbers.txt          # dry run
    python scripts/purge_invalid_numbers.py /path/to/purge_400_numbers.txt --apply  # write

Only sets do_not_call / dnc_reason. Never dials, never deletes a lead.
Matches on the last 10 digits of B2BLead.phone.
"""
import re
import sys

sys.path.insert(0, ".")
from app.database.database import SessionLocal  # noqa: E402
from app.models.models import B2BLead  # noqa: E402


def key(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) > 10 else d


def main():
    path = sys.argv[1]
    apply = "--apply" in sys.argv
    wanted = {key(l) for l in open(path) if key(l)}
    db = SessionLocal()
    hits = [l for l in db.query(B2BLead).all() if key(getattr(l, "phone", "")) in wanted]
    for l in hits:
        print(f"lead {l.id} ******{key(l.phone)[-4:]} dnc={bool(l.do_not_call)}")
        if apply and not l.do_not_call:
            l.do_not_call = True
            l.dnc_reason = "INVALID_NUMBER_2X_SIP_400_PURGE_20261024"
    if apply:
        db.commit()
    print(f"{len(wanted)} purge numbers, {len(hits)} matching leads, {'APPLIED' if apply else 'dry run'}")


if __name__ == "__main__":
    main()
