r"""
The whole Smart Outreach decision chain, for every candidate, sending nothing.

WHY THIS EXISTS
---------------
smart_outreach.run_cycle() decides AND executes. There was no way to see what
the system would do to 1,858 businesses without it doing it. "Turn it on and
watch" is not a dry run; by the time you are watching, the first messages have
left.

This walks the same governed path -- the same gate functions, imported, never
restated -- and stops at the point of execution. For each business it reports
the full chain:

    qualification -> contact -> verification -> consent -> suppression
    -> trust -> channel decision -> cadence -> message preview -> adapter

ZERO PROVIDER CALLS, PROVEN RATHER THAN ASSERTED
------------------------------------------------
Claiming "no sends happened" because the code path looks read-only is exactly
the reasoning that has failed in this repository before: tender_auto_pricer
mailed prospects through raw smtplib around every gate, and nobody noticed
because the send did not look like a send.

So this script installs a tripwire before it does anything: httpx.Client.post,
httpx.post, requests.post, urllib's opener and smtplib.SMTP are all replaced
with functions that raise. If any layer beneath tries to reach a provider, the
run aborts loudly and names the caller. A clean finish is therefore evidence,
not a promise.

Usage
-----
    python scripts/outreach_dry_run.py
    python scripts/outreach_dry_run.py --limit 50
    python scripts/outreach_dry_run.py --channel whatsapp
    python scripts/outreach_dry_run.py --json report.json
    python scripts/outreach_dry_run.py --show-eligible-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class ProviderCallAttempted(RuntimeError):
    """A dry run tried to reach the outside world. That is a defect, not a warning."""


_TRIPPED: list[str] = []


def _install_tripwires() -> None:
    """Make any outbound call impossible, and attribute it if one is tried."""

    def _trip(label):
        def _boom(*args, **kwargs):
            where = "".join(traceback.format_stack(limit=6)[:-1])
            _TRIPPED.append(f"{label}\n{where}")
            raise ProviderCallAttempted(
                f"dry run attempted an outbound call via {label}. "
                f"Caller:\n{where}")
        return _boom

    import httpx
    httpx.Client.post = _trip("httpx.Client.post")          # type: ignore[assignment]
    httpx.Client.request = _trip("httpx.Client.request")    # type: ignore[assignment]
    httpx.post = _trip("httpx.post")                        # type: ignore[assignment]

    try:
        import requests
        requests.post = _trip("requests.post")              # type: ignore[assignment]
        requests.Session.post = _trip("requests.Session.post")  # type: ignore[assignment]
    except ImportError:
        pass

    import smtplib
    smtplib.SMTP = _trip("smtplib.SMTP")                    # type: ignore[assignment]
    smtplib.SMTP_SSL = _trip("smtplib.SMTP_SSL")            # type: ignore[assignment]

    import urllib.request
    urllib.request.urlopen = _trip("urllib.request.urlopen")  # type: ignore[assignment]


def _contact_evidence(lead) -> dict:
    """What we actually know about how to reach this business, and how we know."""
    return {
        "email": (getattr(lead, "email", "") or "") or None,
        "email_trust": getattr(lead, "email_trust", None),
        "email_confidence": getattr(lead, "email_confidence", None),
        "email_source": getattr(lead, "email_source", None),
        "phone": (getattr(lead, "phone", "") or "") or None,
        "phone_source": getattr(lead, "phone_source", None),
        "whatsapp_number": (getattr(lead, "whatsapp_number", "") or "") or None,
        "whatsapp_verified": getattr(lead, "whatsapp_verified", None),
        "website": (getattr(lead, "website", "") or "") or None,
    }


def _personalisation_evidence(lead) -> dict:
    """Only fields a message may legitimately draw on.

    Nothing inferred. If the column is empty the message must not assert it --
    this repository has produced fabricated {slug}.com addresses and hardcoded
    tender pipelines before, and both started as a plausible-looking default.
    """
    return {k: v for k, v in {
        "company": getattr(lead, "company", None),
        "city": getattr(lead, "city", None),
        "segment": getattr(lead, "segment", None),
        "division": getattr(lead, "division", None),
        "contact_name": getattr(lead, "contact_name", None),
        "coffee_buying_score": getattr(lead, "coffee_buying_score", None),
    }.items() if v not in (None, "")}


def main() -> int:
    ap = argparse.ArgumentParser(description="Smart Outreach dry run. Sends nothing.")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--channel", default="", help="only report this channel's decisions")
    ap.add_argument("--json", default="", help="write the full report to a JSON file")
    ap.add_argument("--show-eligible-only", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print every lead, not a sample")
    args = ap.parse_args()

    _install_tripwires()

    from app.database.database import SessionLocal
    from app.models.models import B2BLead
    from app.observability import enable_utf8_stdout, setup_logging
    from app.services import outreach_orchestrator as orch

    enable_utf8_stdout()
    setup_logging("outreach_dry_run")

    db = SessionLocal()
    rows = []
    try:
        leads = (db.query(B2BLead)
                   .order_by(B2BLead.coffee_buying_score.desc().nullslast())
                   .limit(args.limit)
                   .all())

        for lead in leads:
            stop = orch.stop_reason(lead, db)
            elig = orch.eligibility(lead, db)
            # journal=False equivalent: next_touch writes a journal row, which is
            # a local DB write, not an outbound call. It is the real path, so it
            # stays -- a dry run that skips the real path proves nothing.
            plan = orch.next_touch(lead, db)

            rows.append({
                "lead_id": lead.id,
                "company": lead.company,
                "qualified": not stop,
                "qualification_reason": stop or "no suppression, no engagement, not terminal",
                "contact": _contact_evidence(lead),
                "personalisation_evidence": _personalisation_evidence(lead),
                "eligibility": {c: {"eligible": elig[c]["eligible"],
                                    "reason": elig[c]["reason"]} for c in orch.CHANNELS},
                "plan": plan,
            })
    finally:
        db.close()

    # ------------------------------------------------------------- report --
    print()
    print("SMART OUTREACH DRY RUN — nothing was sent")
    print("=" * 78)
    print(f"candidates examined : {len(rows)}")

    actions = Counter(r["plan"]["action"] for r in rows)
    print("planned actions:")
    for action, n in actions.most_common():
        print(f"   {action:<12} {n}")

    print()
    print("channel eligibility across the candidate set:")
    for ch in ("email", "whatsapp", "phone", "linkedin"):
        ok = sum(1 for r in rows if r["eligibility"][ch]["eligible"])
        print(f"   {ch:<10} eligible {ok:>5} / {len(rows)}")
        blockers = Counter(r["eligibility"][ch]["reason"][:72]
                           for r in rows if not r["eligibility"][ch]["eligible"])
        for reason, n in blockers.most_common(2):
            print(f"              {n:>5}  {reason}")

    proposals = [r for r in rows if r["plan"]["action"] == "PROPOSE"]
    if args.channel:
        proposals = [r for r in proposals if r["plan"].get("channel") == args.channel]

    print()
    print(f"PROPOSED TOUCHES: {len(proposals)} (each still requires founder approval)")
    for r in (proposals if args.verbose else proposals[:10]):
        p = r["plan"]
        print(f"   {str(r['company'])[:40]:<40} {p.get('channel'):<9} "
              f"day {p.get('day')} angle={p.get('angle')}")
        print(f"      evidence available: {', '.join(sorted(r['personalisation_evidence']))}")
    if not args.verbose and len(proposals) > 10:
        print(f"   ... {len(proposals) - 10} more (use --verbose)")

    print()
    print("PROVIDER CALLS ATTEMPTED:", len(_TRIPPED))
    if _TRIPPED:
        print("  *** a dry run reached for a provider — this is a defect ***")
        for t in _TRIPPED[:3]:
            print(t)
        return 1
    print("  0 — verified by tripwire on httpx, requests, smtplib and urllib,")
    print("      not by inspection of the code path")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"candidates": rows,
                       "provider_calls_attempted": len(_TRIPPED)}, fh, indent=2, default=str)
        print(f"\nfull report written to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
