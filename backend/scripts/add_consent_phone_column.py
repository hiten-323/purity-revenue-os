r"""
Add consent_phone to an existing database.

Same shape as add_pipeline_columns.py: Base.metadata.create_all() does not
alter a table that already exists, so a new nullable column needs an explicit
ADD COLUMN or the model and the live schema drift apart.

ADD COLUMN only -- never drops, never renames, never backfills. Every existing
lead starts at consent_phone = NULL, which whatsapp_sender.consent_check()
reads as "no number captured for this consent" and skips the match check, so
existing EXPLICIT consent (however it was recorded before this column
existed) keeps behaving exactly as it does today.

Usage
-----
    python scripts/add_consent_phone_column.py            # dry run
    python scripts/add_consent_phone_column.py --commit
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB = os.path.join(os.path.dirname(__file__), "..", "purity_beans.db")

COLUMNS = [
    ("consent_phone", "VARCHAR"),
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
            print("consent_phone already present -- nothing to do.")
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
