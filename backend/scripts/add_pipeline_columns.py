r"""
Add the founder-call pipeline columns to an existing database.

There is no Alembic here, and Base.metadata.create_all() does not alter a table
that already exists -- it silently leaves the new columns off, which is how a
model and a live schema drift apart without anyone noticing.

This does ADD COLUMN and nothing else. It never drops, never renames, never
backfills a value that has to be earned: every existing lead starts at
outreach_stage = NULL, which the pipeline reads as ELIGIBLE, and
ai_call_count = 0. That is accurate -- no AI call has been placed against any
of them.

Usage
-----
    python scripts/add_pipeline_columns.py            # dry run
    python scripts/add_pipeline_columns.py --commit
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB = os.path.join(os.path.dirname(__file__), "..", "purity_beans.db")

COLUMNS = [
    ("outreach_stage", "VARCHAR"),
    ("outreach_stage_at", "DATETIME"),
    ("ai_call_count", "INTEGER DEFAULT 0"),
    ("ai_interest_level", "VARCHAR"),
    ("founder_callback_window", "VARCHAR"),
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
        have = {r[1] for r in conn.execute("PRAGMA table_info(b2b_leads)")}
        todo = [(n, t) for n, t in COLUMNS if n not in have]

        if not todo:
            print("all %d pipeline columns already present -- nothing to do."
                  % len(COLUMNS))
            return 0

        for name, decl in todo:
            sql = "ALTER TABLE b2b_leads ADD COLUMN %s %s" % (name, decl)
            print(("APPLY  " if args.commit else "would ") + sql)
            if args.commit:
                conn.execute(sql)
        if args.commit:
            conn.commit()
            print("\n%d column(s) added. No row values were changed." % len(todo))
        else:
            print("\nDRY RUN -- nothing written. Re-run with --commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
