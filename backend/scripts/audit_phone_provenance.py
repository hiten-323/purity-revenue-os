"""
Audit every phone number against its provenance.

The rule
--------
    FIRST_PARTY  the brand, the business, or the founder stated it.
                 Immutable unless explicit correction evidence arrives.
    SEARCH       an engine or directory listed it. A candidate to dial,
                 never authoritative on its own.

Why this had to run
-------------------
1,216 leads carried phone_verified=True whose only evidence was that Perplexity
or BraveSearch listed the number. phone_verified is not a note — decision_engine
(:36, :429), outreach_engine (:527) and revenue_engine (:156-157) read it as
"phone/WhatsApp is an available channel". So the database was asserting a
verification nobody had performed, on 83% of its phone-bearing rows.

None of them had been dialled yet, which is the only reason this is a
correction and not a post-mortem.

What it changes
---------------
* phone_verified -> False where provenance is SEARCH or UNSOURCED.
  The NUMBER is kept: it is still the best lead we have and is exactly what the
  founder call sheet is for. Only the claim of verification is withdrawn.
* phone_trust -> DISCOVERED (listed, unconfirmed) where it was UNKNOWN.
* whatsapp_number cleared where the number is a landline — WhatsApp cannot
  reach 0172/0161/022 lines, so those were queued undeliverable.
* one LeadEvidence row per change, so the correction is auditable rather than
  another silent rewrite.

Nothing is deleted and no number is altered. A founder call can restore
phone_verified through the normal import path, which is the explicit correction
evidence the rule asks for.

Usage
-----
    python scripts/audit_phone_provenance.py            # dry run
    python scripts/audit_phone_provenance.py --commit
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    from app.database.database import SessionLocal
    from app.models.models import B2BLead, LeadEvidence
    from app.services.contact_enricher import (FIRST_PARTY, is_landline,
                                               phone_provenance)

    db = SessionLocal()
    tally = collections.Counter()
    unverified = wa_cleared = trust_set = 0
    try:
        leads = db.query(B2BLead).filter(
            B2BLead.phone.isnot(None), B2BLead.phone != "").all()

        for lead in leads:
            prov = phone_provenance(lead)
            tally[prov] += 1
            if prov == FIRST_PARTY:
                continue

            notes = []
            if getattr(lead, "phone_verified", False):
                if args.commit:
                    lead.phone_verified = False
                unverified += 1
                notes.append("withdrew phone_verified (listed by search, "
                             "not confirmed by the business)")

            if (getattr(lead, "phone_trust", "") or "UNKNOWN").upper() == "UNKNOWN":
                if args.commit:
                    lead.phone_trust = "DISCOVERED"
                trust_set += 1

            if lead.whatsapp_number and is_landline(lead.whatsapp_number):
                if args.commit:
                    lead.whatsapp_number = None
                wa_cleared += 1
                notes.append("cleared landline from whatsapp_number "
                             "(WhatsApp cannot reach an STD line)")

            if notes and args.commit:
                db.add(LeadEvidence(
                    lead_id=lead.id, signal_type="PHONE_PROVENANCE_AUDIT",
                    value="; ".join(notes),
                    source=(lead.phone_source or "")[:200] or "unsourced",
                    collected_at=datetime.utcnow(), verified=False))

        if args.commit:
            db.commit()
    finally:
        db.close()

    print(f"phones examined        : {sum(tally.values())}")
    for k, v in tally.most_common():
        print(f"   {k:<14}{v}")
    print()
    print(f"phone_verified withdrawn : {unverified}")
    print(f"phone_trust -> DISCOVERED: {trust_set}")
    print(f"landline WhatsApp cleared: {wa_cleared}")
    print()
    print("Numbers themselves are untouched — they remain the call queue.")
    if not args.commit:
        print("DRY RUN — nothing written. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
