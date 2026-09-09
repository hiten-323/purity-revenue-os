"""
Scrapling dry run — read a few first-party websites, write absolutely nothing.

Why this exists
---------------
Before SCRAPLING_ENABLED=1 reaches a worker that can write to leads, someone
has to answer three questions with evidence rather than with reasoning:

    does the MCP client connect at all?
    what does it actually extract from a real site?
    is the database genuinely untouched?

Read-only by construction
-------------------------
This calls harvest_one(website), NOT harvest(db, ...). harvest_one takes no
database handle, so it has nothing to write to; the isolation is structural
rather than a flag someone can forget. The lead table is opened read-only
(mode=ro) purely to choose which sites to visit.

Proof, not assertion
--------------------
A SHA-256 digest of every lead's contact columns plus the workflow_events count
is taken before and after. If either moves, the run is reported as FAILED even
though nothing here can write — because something else would have, and that is
worth knowing before enabling the real harvester.

One caveat when reading the output: the live worker writes heartbeats
continuously, so the baseline differs between runs. What matters is that the
before and after of a SINGLE run match.

Stealth stays off unless SCRAPLING_ALLOW_STEALTH is explicitly set; this script
pins it to "0" regardless, since a dry run is not the place to discover what a
site's bot defences do.

Usage
-----
    python scripts/scrapling_dry_run.py                 # 5 leads
    python scripts/scrapling_dry_run.py --limit 10
    SCRAPLING_ENABLED=1 python scripts/scrapling_dry_run.py
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB = os.path.join(os.path.dirname(__file__), "..", "purity_beans.db")


def _digest() -> tuple[str, int]:
    """Fingerprint every contact column a harvester could plausibly touch."""
    conn = sqlite3.connect(f"file:{os.path.abspath(DB)}?mode=ro", uri=True, timeout=30)
    rows = conn.execute(
        "SELECT id, email, phone, whatsapp_number, email_trust, email_source, "
        "phone_source, phone_verified FROM b2b_leads ORDER BY id"
    ).fetchall()
    events = conn.execute("SELECT count(*) FROM workflow_events").fetchone()[0]
    conn.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16], events


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Scrapling dry run.")
    ap.add_argument("--limit", type=int, default=5, help="how many leads to visit")
    args = ap.parse_args()

    # A dry run is not the place to find out what a site's bot defences do.
    os.environ["SCRAPLING_ALLOW_STEALTH"] = "0"

    before, events_before = _digest()
    print(f"baseline   leads-digest={before}  workflow_events={events_before}")

    conn = sqlite3.connect(f"file:{os.path.abspath(DB)}?mode=ro", uri=True, timeout=30)
    leads = conn.execute(
        "SELECT id, company, website, email, phone FROM b2b_leads "
        "WHERE trim(coalesce(website,'')) <> '' ORDER BY id LIMIT ?",
        (max(1, args.limit),),
    ).fetchall()
    conn.close()

    from app.services import scrapling_client as client
    from app.services.scrapling_harvester import harvest_one

    print(f"scrapling  enabled={client.enabled()}  endpoint={client.endpoint()}")
    print(f"visiting   {len(leads)} lead(s), stealth off\n")

    reached = extracted = 0
    for lead_id, company, website, email, phone in leads:
        print(f"lead {lead_id}  {str(company)[:40]}")
        print(f"   site           : {website}")
        print(f"   on record      : email={email or '(none)'}  phone={phone or '(none)'}")
        try:
            hit = harvest_one(website)
        except Exception as exc:  # noqa: BLE001 - a dry run reports, never raises
            print(f"   RAISED         : {exc.__class__.__name__}: {str(exc)[:110]}\n")
            continue

        pages = len(hit.get("pages") or [])
        if pages:
            reached += 1
        if hit.get("emails") or hit.get("phones"):
            extracted += 1
        print(f"   pages fetched  : {pages}")
        print(f"   emails found   : {hit.get('emails')}")
        print(f"   phones found   : {hit.get('phones')}  whatsapp={hit.get('whatsapp') or '(none)'}")
        if hit.get("error"):
            print(f"   error          : {str(hit['error'])[:110]}")
        print()

    after, events_after = _digest()
    print(f"after      leads-digest={after}  workflow_events={events_after}")
    print()
    print(f"sites reached      : {reached}/{len(leads)}")
    print(f"sites with contact : {extracted}/{len(leads)}")

    clean = after == before and events_after == events_before
    print()
    if clean:
        print("ZERO WRITES CONFIRMED — lead rows and workflow_events both unchanged.")
    else:
        print("*** DATABASE CHANGED DURING A DRY RUN ***")
        print("    Nothing in this script can write, so another process did.")
        print("    Find it before enabling the real harvester.")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
