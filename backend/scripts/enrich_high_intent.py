"""
High-Intent Enrichment Runner for Purity Revenue OS.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/enrich_high_intent.py --limit 20
  PYTHONPATH=. .venv/bin/python scripts/enrich_high_intent.py --lead-id 1804
  PYTHONPATH=. .venv/bin/python scripts/enrich_high_intent.py --priority --limit 15
"""
from __future__ import annotations

import argparse
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("enrich_high_intent")

from app.database.database import SessionLocal
from app.models.models import B2BLead
from app.services.enrichment_engine import enrich_lead, enrich_batch
from sqlalchemy import or_


def main():
    parser = argparse.ArgumentParser(description="Enrich B2B coffee leads with buying signals and contacts")
    parser.add_argument("--limit", type=int, default=20, help="Number of leads to enrich (default: 20)")
    parser.add_argument("--lead-id", type=int, default=None, help="Specific lead ID to enrich")
    parser.add_argument("--priority", action="store_true", help="Filter for high fit leads (coffee_buying_score >= 70)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate enrichment without writing to database")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.lead_id:
            leads = db.query(B2BLead).filter(B2BLead.id == args.lead_id).all()
            if not leads:
                logger.error("Lead ID %s not found in database.", args.lead_id)
                sys.exit(1)
        else:
            q = db.query(B2BLead).filter(
                or_(
                    B2BLead.intelligence_status != "ENRICHED",
                    B2BLead.intelligence_status == None,
                    B2BLead.email == None,
                )
            )
            if args.priority:
                q = q.filter(B2BLead.coffee_buying_score >= 70)

            leads = q.order_by(
                B2BLead.coffee_buying_score.desc().nullslast(),
                B2BLead.id.asc()
            ).limit(args.limit).all()

        logger.info("Found %d lead(s) for high-intent enrichment.", len(leads))
        if not leads:
            logger.info("No leads require enrichment at this time.")
            return

        if args.dry_run:
            logger.info("[DRY-RUN] Simulating enrichment without saving to database...")
            for l in leads:
                res = enrich_lead(
                    company=l.company or "",
                    city=l.city or "",
                    website=l.website or "",
                    division=l.division or "",
                )
                logger.info(
                    "[DRY-RUN] Lead %d: %s (%s) -> Website: %s, Emails: %s, Phones: %s, Signals: %s, Brands: %s",
                    l.id, l.company, l.city, res["website"], res["emails"], res["phones"],
                    [k for k, v in res["coffee_signals"].items() if v],
                    res["detected_brands"]
                )
            return

        stats = enrich_batch(leads, db, max_per_run=len(leads))
        logger.info(
            "Enrichment Complete: Enriched=%d, Skipped=%d, Failed=%d, Priority A=%d, Priority B=%d",
            stats["enriched"], stats["skipped"], stats["failed"],
            stats["promoted_priority_a"], stats["promoted_priority_b"]
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
