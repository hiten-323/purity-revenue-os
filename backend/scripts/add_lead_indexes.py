r"""
Index the columns this system actually filters on.

b2b_leads carried two indexes: `company` (unique) and `id`. Everything else
was a full table scan, including the ones on hot paths:

    email           contact_trust's shared-inbox check runs one lookup per
                    lead with an address, 202 of them per sweep, and the sweep
                    runs at boot and on the worker cadence.
                    EXPLAIN said SCAN b2b_leads.
    phone           import_call_sheet matches every filled row on phone.
    email_trust     the send queue filters on it constantly.
    outreach_stage  the founder queue filters on it.

At 1,858 rows each scan is only a few milliseconds, which is exactly why this
was never noticed. It is also linear in the lead count, and the entire scraper
effort exists to increase that count.

Indexes are additive and reversible. No row is read or written.

Usage
-----
    python scripts/add_lead_indexes.py            # dry run
    python scripts/add_lead_indexes.py --commit
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB = os.path.join(os.path.dirname(__file__), "..", "purity_beans.db")

INDEXES = [
    ("ix_b2b_leads_email", "email"),
    ("ix_b2b_leads_phone", "phone"),
    ("ix_b2b_leads_email_trust", "email_trust"),
    ("ix_b2b_leads_outreach_stage", "outreach_stage"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    path = os.path.abspath(DB)
    if not os.path.exists(path):
        raise SystemExit("no database at %s" % path)

    conn = sqlite3.connect(path, timeout=30)
    try:
        have = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='index'")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(b2b_leads)")}

        todo = []
        for name, col in INDEXES:
            if name in have:
                print("  exists   %s" % name)
            elif col not in cols:
                print("  SKIP     %s -- column %r not in b2b_leads" % (name, col))
            else:
                todo.append((name, col))

        for name, col in todo:
            sql = "CREATE INDEX %s ON b2b_leads (%s)" % (name, col)
            print(("  CREATE   " if args.commit else "  would   ") + sql)
            if args.commit:
                conn.execute(sql)

        if args.commit and todo:
            conn.execute("ANALYZE")
            conn.commit()
            print("\n%d index(es) created, ANALYZE run. No row was modified." % len(todo))
        elif not todo:
            print("\nNothing to do.")
        else:
            print("\nDRY RUN -- nothing written. Re-run with --commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
