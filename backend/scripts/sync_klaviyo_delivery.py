#!/usr/bin/env python3
"""Reconcile provider-confirmed WhatsApp failures; never sends messages."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.database import SessionLocal
from app.services.klaviyo_sync import sync_klaviyo_delivery_events

def main():
    db = SessionLocal()
    try:
        result = sync_klaviyo_delivery_events(db)
        print("KLAVIYO_DELIVERY_RECONCILIATION", result)
        return 0
    except Exception as exc:
        # Do not print request headers or credentials.
        print("KLAVIYO_DELIVERY_RECONCILIATION_ERROR", type(exc).__name__, str(exc)[:180])
        return 1
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
