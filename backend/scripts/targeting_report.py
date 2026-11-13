"""
Targeting-quality report — the learning dataset for outreach.

One row per attempted touch, joining what we BELIEVED about the lead when we
chose it to what actually happened. Without this the only record of a cycle is
a pm2 log line that scrolls away, and the system cannot learn which categories,
messages or channels earn replies.

Read-only. Judges the engine on qualified opportunities per 100 eligible leads,
not on emails sent — volume is an input, not an outcome.

    python scripts/targeting_report.py            # summary
    python scripts/targeting_report.py --rows     # per-touch detail
    python scripts/targeting_report.py --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass

import app.models.models        # noqa: F401
from app.database.database import SessionLocal
from app.models.models import B2BLead, WorkflowEvent
from app.services.smart_outreach import OutreachTouch
from app.services import territory as T

# Outcomes worth learning from, weakest to strongest. A reply beats an open;
# an order beats a reply. Opens are deliberately excluded — they are a vanity
# metric and, with image proxies, not even a reliable one.
OUTCOME_EVENTS = {
    "EMAIL_REPLY_RECEIVED": "reply",
    "REPLY_RECEIVED": "reply",
    "WHATSAPP_REPLY": "reply",
    "CATALOGUE_REQUESTED": "catalogue",
    "SAMPLE_REQUESTED": "sample",
    "MEETING_HELD": "meeting",
    "ORDER_PLACED": "order",
    "LEAD_DISQUALIFIED": "negative",
    "UNSUBSCRIBED": "negative",
}
PROVEN = ("SENT", "PROVIDER_ACCEPTED", "DELIVERED", "READ")


def build(db) -> list[dict]:
    touches = db.query(OutreachTouch).order_by(OutreachTouch.occurred_at.asc()).all()
    if not touches:
        return []
    ids = {t.lead_id for t in touches}
    leads = {l.id: l for l in db.query(B2BLead).filter(B2BLead.id.in_(ids)).all()}

    # Outcomes AFTER each touch, so a reply is attributed to the touch that
    # preceded it rather than to the whole lead.
    evs = defaultdict(list)
    for e in db.query(WorkflowEvent).filter(WorkflowEvent.lead_id.in_(ids)).all():
        if e.event_type in OUTCOME_EVENTS:
            evs[e.lead_id].append(e)

    rows = []
    for t in touches:
        l = leads.get(t.lead_id)
        if not l:
            continue
        after = [e for e in evs.get(t.lead_id, [])
                 if (e.occurred_at or 0) and t.occurred_at and e.occurred_at > t.occurred_at]
        outcome = ""
        for e in sorted(after, key=lambda x: x.occurred_at):
            outcome = OUTCOME_EVENTS[e.event_type]
            break
        rows.append({
            "occurred_at": t.occurred_at.isoformat() if t.occurred_at else "",
            "lead_id": t.lead_id,
            "company": (l.company or "")[:48],
            "category": l.division or "",
            "fit": l.coffee_buying_score or 0,
            "city": l.city or "",
            "territory": T.territory_of(l),
            "channel": t.channel or "",
            "touch_type": t.touch_type or "",
            "template": t.template_key or "",
            "status": t.status or "",
            "proven": "yes" if (t.status or "") in PROVEN else "no",
            "has_msgid": "yes" if t.provider_message_id else "no",
            "outcome": outcome or "none_yet",
        })
    return rows


def summarise(rows: list[dict]) -> None:
    if not rows:
        print("  no touches recorded yet — nothing to learn from")
        return
    proven = [r for r in rows if r["proven"] == "yes"]
    qualified = [r for r in rows if r["outcome"] in ("reply", "catalogue", "sample",
                                                     "meeting", "order")]
    print(f"  touches attempted        {len(rows)}")
    print(f"  proven sends             {len(proven)}")
    print(f"  qualified opportunities  {len(qualified)}")
    if proven:
        print(f"  qualified per 100 proven {100 * len(qualified) / len(proven):.1f}")
    else:
        print("  qualified per 100 proven n/a — no proven send yet")

    print("\n  by status:")
    for k, n in Counter(r["status"] for r in rows).most_common():
        print(f"    {k:<18}{n}")

    print("\n  by category (proven sends -> qualified):")
    by = defaultdict(lambda: [0, 0])
    for r in proven:
        by[r["category"] or "unknown"][0] += 1
        if r["outcome"] in ("reply", "catalogue", "sample", "meeting", "order"):
            by[r["category"] or "unknown"][1] += 1
    for cat, (sent, q) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        rate = f"{100*q/sent:.0f}%" if sent else "—"
        print(f"    {cat[:22]:<24}{sent:>4} sent{q:>5} qualified   {rate}")

    print("\n  by fit band (proven sends -> qualified):")
    bands = [(90, "90-100 cafe/hotel"), (70, "70-89 restaurant/dist"),
             (40, "40-69 office/other"), (0, "unclassified")]
    for lo, label in bands:
        hi = 101 if lo == 90 else (90 if lo == 70 else (70 if lo == 40 else 40))
        seg = [r for r in proven if lo <= r["fit"] < hi] if lo else [r for r in proven if not r["fit"]]
        q = [r for r in seg if r["outcome"] in ("reply", "catalogue", "sample", "meeting", "order")]
        if seg:
            print(f"    {label:<24}{len(seg):>4} sent{len(q):>5} qualified")

    unknown = [r for r in rows if r["outcome"] == "none_yet" and r["proven"] == "yes"]
    if unknown:
        print(f"\n  {len(unknown)} proven send(s) with no outcome yet — too early to judge")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", action="store_true")
    ap.add_argument("--csv")
    a = ap.parse_args()

    db = SessionLocal()
    rows = build(db)
    print("TARGETING QUALITY REPORT\n")
    summarise(rows)
    if a.rows and rows:
        print("\n  per-touch:")
        for r in rows:
            print(f"    {r['occurred_at'][:16]}  fit {r['fit']:<4}{r['category'][:14]:<16}"
                  f"{r['company'][:30]:<32}{r['touch_type'][:16]:<18}{r['status']:<10}{r['outcome']}")
    if a.csv and rows:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"\n  wrote {len(rows)} rows to {a.csv}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
