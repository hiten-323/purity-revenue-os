"""27 Oct 2026: mark leads the pre-dial filter refuses as do_not_call.

    python scripts/mark_predial_dnc.py                      # dry run
    python scripts/mark_predial_dnc.py --apply              # DEAD_SERIES -> do_not_call
    python scripts/mark_predial_dnc.py --apply --invalid    # also INVALID_NUMBER (landline /
                                                            # toll-free / malformed) -> do_not_call

--invalid is opt-in because do_not_call also stops email/WhatsApp outreach
(outreach_orchestrator.stop_reason), and the 8 Oct founder rule keeps
landline-only leads email-eligible. Leads with no phone at all are skipped.
Only sets do_not_call / dnc_reason. Never dials, never deletes a lead.
"""
import sys
from collections import Counter

sys.path.insert(0, ".")
from app.database.database import SessionLocal  # noqa: E402
from app.models.models import B2BLead  # noqa: E402
from app.services.founder_call_pipeline import predial_block  # noqa: E402


def main():
    apply = "--apply" in sys.argv
    with_invalid = "--invalid" in sys.argv
    db = SessionLocal()
    cnt = Counter()
    for l in db.query(B2BLead).all():
        if not (l.phone or "").strip():
            continue
        code = predial_block(l.phone)
        if not code:
            continue
        state = "already_dnc" if l.do_not_call else "open"
        cnt[(code, state)] += 1
        will = (not l.do_not_call) and (code == "DEAD_SERIES" or with_invalid)
        print(f"lead {l.id} {code} ******{''.join(ch for ch in l.phone if ch.isdigit())[-4:]} "
              f"dnc={bool(l.do_not_call)}{' -> MARK' if will else ''}")
        if apply and will:
            l.do_not_call = True
            l.dnc_reason = code
            cnt[(code, "marked")] += 1
    if apply:
        db.commit()
    print(dict(cnt), "APPLIED" if apply else "dry run")


if __name__ == "__main__":
    main()
