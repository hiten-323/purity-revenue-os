"""
Controlled single send — the last gate before enabling automation.

Proves the full outbound path on the new runtime: SMTP auth with the rotated
credential, a provider message-id, and an EMAIL_SENT row that satisfies the
fail-closed proof listener. Anything less and "automation is safe to enable" is
an assumption rather than a measurement.

DRY RUN BY DEFAULT. Nothing leaves the building without --send.

  python scripts/controlled_send.py --to you@example.com
  python scripts/controlled_send.py --to you@example.com --send
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass

import app.models.models            # noqa: F401
import app.models.send_proof_fix    # noqa: F401  fail-closed EMAIL_SENT proof
from app.models.models import WorkflowEvent
from app.database.database import SessionLocal
from app.services.email_sender import OutreachEmail, send_email, domain_is_deliverable

SUBJECT = "Purity Beans — runtime verification"
BODY = (
    "This is a controlled verification send from the Purity Beans Revenue OS.\n\n"
    "It confirms three things on the new runtime:\n"
    "  1. SMTP authenticates with the rotated Zoho credential\n"
    "  2. the provider returns a message-id\n"
    "  3. the send is recorded with proof, not merely assumed\n\n"
    "No prospect received this message.\n"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="recipient (use your own address)")
    ap.add_argument("--send", action="store_true", help="actually send; omit for dry run")
    a = ap.parse_args()

    print("CONTROLLED SEND" + ("" if a.send else "  (DRY RUN — nothing will be sent)"))
    print(f"  to      : {a.to}")
    print(f"  from    : {os.getenv('SENDER_EMAIL', '(SENDER_EMAIL unset)')}")
    print(f"  subject : {SUBJECT}")

    # Same NXDOMAIN gate the real path uses.
    try:
        ok = domain_is_deliverable(a.to)
        print(f"  domain  : {'resolves' if ok else 'DOES NOT RESOLVE — send would be blocked'}")
        if not ok:
            return 1
    except Exception as e:
        print(f"  domain  : check failed ({e.__class__.__name__})")
        return 1

    if not (os.getenv("ZOHO_APP_PASSWORD") or "").strip():
        print("  FAIL: ZOHO_APP_PASSWORD not loaded — send would fail")
        return 1
    print("  smtp    : credential present")

    if not a.send:
        print("\nDry run complete. Re-run with --send to deliver.")
        return 0

    db = SessionLocal()
    before = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_SENT").count()

    email = OutreachEmail(to_email=a.to, to_name="Hiten", company="Pure Pantry Provisions",
                          subject=SUBJECT, body_text=BODY)
    result = send_email(email)

    print(f"\n  status  : {result.status}")
    if result.error:
        print(f"  error   : {result.error}")
    # The id itself is not a secret, but print only its presence and shape.
    print(f"  msg-id  : {'present' if result.message_id else 'ABSENT — no delivery proof'}")

    # Record the proof event, because send_email does not.
    #
    # send_email reaches SMTP and returns; writing EMAIL_SENT is the CALLER's
    # job — a comment in that module notes seven call sites reach SMTP and only
    # five record it. So a "successful" send that nobody records leaves no
    # evidence at all, and the cadence/account-cap logic that reads EMAIL_SENT
    # sees a lead that was never contacted.
    #
    # Writing it here also exercises the fail-closed listener end to end: with
    # a real recipient and a real provider message-id it must survive as
    # EMAIL_SENT; without them it would be renamed EMAIL_SENT_UNPROVEN.
    if result.status == "sent" and result.message_id:
        db.add(WorkflowEvent(
            event_type="EMAIL_SENT", actor="CONTROLLED_SEND", channel="email",
            payload={"to": result.to_email, "message_id": result.message_id,
                     "subject": SUBJECT, "verification": True},
            occurred_at=datetime.utcnow()))
        db.commit()

    after = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_SENT").count()
    unproven = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "EMAIL_SENT_UNPROVEN").count()
    print(f"  EMAIL_SENT rows : {before} -> {after}")
    print(f"  EMAIL_SENT_UNPROVEN total : {unproven}")
    db.close()

    good = result.status == "sent" and bool(result.message_id) and after == before + 1
    print("\n" + ("PASS — proven send recorded" if good
                  else "FAIL — see status/error above; do NOT enable automation"))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
