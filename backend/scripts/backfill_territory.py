"""
Backfill distance_from_origin and origin_city from lat/long already on record.

latitude/longitude are present on 1,717 of 1,831 leads, but
distance_from_origin was populated on 17 — so every territory question required
recomputing haversine over the whole table. This writes it once.

Read-only by default. --write to persist.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND, ".env"))
except Exception:
    pass

import app.models.models        # noqa: F401
from app.database.database import SessionLocal
from app.models.models import B2BLead
from app.services import territory as T


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist (default: dry run)")
    a = ap.parse_args()

    db = SessionLocal()
    leads = db.query(B2BLead).all()
    terr, changed, ungeocoded = Counter(), 0, 0

    for l in leads:
        d = T.distance_from_origin(l)
        t = T.territory_of(l)
        terr[t] += 1
        if d is None:
            ungeocoded += 1
            continue
        if l.distance_from_origin != d or (l.origin_city or "") != T.ORIGIN_CITY:
            changed += 1
            if a.write:
                l.distance_from_origin = d
                l.origin_city = T.ORIGIN_CITY

    if a.write:
        db.commit()

    print(f"{'WROTE' if a.write else 'DRY RUN'} — {len(leads)} leads\n")
    print(f"  distance/origin rows to set : {changed}")
    print(f"  not geocoded (skipped)      : {ungeocoded}\n")
    print(f"  {'territory':<16}{'leads':>7}{'multiplier':>12}")
    for name, n in sorted(terr.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<16}{n:>7}{T.MULTIPLIER.get(name, 1.0):>12}")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
