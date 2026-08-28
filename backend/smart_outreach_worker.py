"""Dedicated adaptive outreach worker.

Runs independently from FastAPI so enrichment/outreach cannot starve the API
loop. Both outreach switches must be enabled explicitly. The worker never
bypasses the smart_outreach service's suppression, trust, provider or WhatsApp
consent checks.
"""
from __future__ import annotations

import os
import time

from app.database.database import SessionLocal
from app.services.smart_outreach import run_cycle


INTERVAL_SECONDS = max(300, int(os.getenv("OUTREACH_INTERVAL_SECONDS", "900")))
BATCH_SIZE = max(1, min(100, int(os.getenv("OUTREACH_BATCH_SIZE", "20"))))


def main() -> None:
    if os.getenv("AUTO_OUTREACH_ENABLED", "0") != "1" or os.getenv("SMART_OUTREACH_ENABLED", "0") != "1":
        print("[smart-outreach] disabled: AUTO_OUTREACH_ENABLED and SMART_OUTREACH_ENABLED must both be 1")
        return

    print(f"[smart-outreach] enabled; interval={INTERVAL_SECONDS}s batch={BATCH_SIZE}")
    while True:
        db = SessionLocal()
        try:
            result = run_cycle(db, limit=BATCH_SIZE)
            print(f"[smart-outreach] {result}")
        except Exception as exc:
            print(f"[smart-outreach] cycle failed: {type(exc).__name__}: {exc}")
        finally:
            db.close()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
