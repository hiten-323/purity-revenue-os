"""
Sweep Intent Tiers across B2BLead table.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/sweep_intent_tiers.py
"""
import sys
import logging
from app.database.database import SessionLocal
from app.services.high_intent_scorer import sweep_intent_scores

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("sweep_intent_tiers")

def main():
    db = SessionLocal()
    try:
        logger.info("Starting database-wide intent score and tier sweep...")
        res = sweep_intent_scores(db)
        logger.info(
            "Sweep completed successfully across %d leads:\n"
            "  - Priority A (80-100) : %d\n"
            "  - Priority B (60-79)  : %d\n"
            "  - Nurture (40-59)     : %d\n"
            "  - Below 40            : %d\n",
            res["total"], res["Priority A"], res["Priority B"], res["Nurture"], res["Below 40"]
        )
    finally:
        db.close()

if __name__ == "__main__":
    main()
