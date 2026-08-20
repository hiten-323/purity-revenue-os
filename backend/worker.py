"""
Auto-Warm background worker — runs in its OWN process.

Each cycle syncs replies, updates conversation memory, classifies leads, and
— only when SMART_OUTREACH_ENABLED is explicitly on — executes due
consent-safe automatic email/WhatsApp outreach and learns measured
category/channel reply rates.

Ordinary outreach requires no founder approval. Commercial pricing/discount
policy remains governed by its existing gates.

Default: SMART_OUTREACH_ENABLED=0 so merge ≠ live sending.
"""
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s [auto-warm] %(message)s", stream=sys.stdout)

if __name__ == "__main__":
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401  — fail-closed EMAIL_SENT proof

    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()

    cycle_sec = int(os.getenv("SMART_OUTREACH_CYCLE_SECONDS", "600"))
    # Default OFF: production must opt in after /health + controlled send proof.
    enabled = os.getenv("SMART_OUTREACH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    limit = max(1, min(100, int(os.getenv("SMART_OUTREACH_LIMIT", "20"))))
    logging.info("Adaptive outreach: enabled=%s limit=%s cycle=%ss", enabled, limit, cycle_sec)

    while True:
        db = SessionLocal()
        try:
            try:
                from app.api.endpoints import sync_email_replies

                rep = sync_email_replies(days=7, db=db)
                logging.info("reply sync: %s", rep)
            except Exception as e:
                logging.error("reply sync failed: %s", e)
                rep = {"error": str(e)}

            if enabled:
                try:
                    from app.services.outreach_lifecycle import run_automatic_cycle

                    result = run_automatic_cycle(db, limit=limit)
                    logging.info(
                        "smart outreach: processed=%s memory=%s learning=%s",
                        result.get("processed"),
                        result.get("memory"),
                        result.get("learning"),
                    )
                except Exception as e:
                    logging.error("smart outreach cycle failed: %s", e)
            else:
                logging.info("smart outreach disabled (set SMART_OUTREACH_ENABLED=1 after runtime gate)")

            try:
                from app.services.sequence_engine import prepare_due_drafts

                prep = prepare_due_drafts(db)
                if prep.get("created") or prep.get("due"):
                    logging.info(
                        "legacy sequence drafts: created=%s due=%s skipped=%s",
                        prep.get("created"),
                        prep.get("due"),
                        prep.get("skipped"),
                    )
            except Exception as e:
                logging.error("sequence prepare failed: %s", e)

            try:
                result = _relearn_patterns(db)
                logging.info("re-learned legacy patterns: %s", result)
            except Exception as e:
                logging.error("legacy relearn failed: %s", e)

            try:
                from app.services.heartbeat import beat

                beat(
                    "worker",
                    db,
                    {
                        "reply_sync": str(rep)[:120],
                        "smart_outreach": "enabled" if enabled else "disabled",
                    },
                )
                beat(
                    "smart_outreach",
                    db,
                    {
                        "note": "classify -> evaluate_next_action -> execute -> learn",
                        "limit": limit,
                        "enabled": enabled,
                    },
                )
            except Exception as e:
                logging.error("heartbeat failed: %s", e)
        except Exception as e:
            logging.error("worker cycle failed: %s", e)
        finally:
            db.close()
        time.sleep(cycle_sec)
