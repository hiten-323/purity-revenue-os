"""
Auto-Warm background worker — runs in its OWN process.

OUTREACH AUTOMATION
Each cycle:
  1. Pull real replies from Zoho IMAP (exit sequences on engagement)
  2. Prepare due sequence drafts for founder approval (nothing sends)
  3. Drain the approved send queue (founder already said yes)
  4. Re-learn patterns from outcomes

The founder's only required action is approve/reject at /api/v1/founder/pending.
Those decisions land in the founder_actions table.
"""
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [auto-warm] %(message)s",
    stream=sys.stdout,
)

if __name__ == "__main__":
    # Fail-closed EMAIL_SENT proof before any drain can write events.
    # Worker does not run FastAPI startup, so this import is required here.
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401

    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()

    CYCLE_SEC = 600
    while True:
        try:
            db = SessionLocal()
            try:
                from app.api.endpoints import sync_email_replies
                rep = sync_email_replies(days=7, db=db)
                logging.info(f"reply sync: {rep}")
            except Exception as e:
                logging.error(f"reply sync failed: {e}")
                rep = {"error": str(e)}

            try:
                from app.services.sequence_engine import prepare_due_drafts
                prep = prepare_due_drafts(db)
                if prep.get("created") or prep.get("due"):
                    logging.info(
                        f"sequence drafts: created={prep.get('created')} "
                        f"due={prep.get('due')} skipped={prep.get('skipped')} "
                        f"— {prep.get('founder_next')}"
                    )
            except Exception as e:
                logging.error(f"sequence prepare failed: {e}")

            try:
                from app.services.send_queue import drain
                q = drain(db)
                if q.get("approved_waiting") or q.get("sent"):
                    logging.info(
                        f"send queue: {q.get('sent')} sent, "
                        f"{q.get('held')} held, {q.get('blocked')} blocked, "
                        f"{q.get('approved_waiting')} approved waiting"
                    )
            except Exception as e:
                logging.error(f"send queue drain failed: {e}")

            try:
                from app.services.heartbeat import beat
                beat("worker", db, {
                    "reply_sync": str(rep)[:120],
                    "sequence": "prepared",
                    "send_queue": "drained",
                })
                beat("sequence_engine", db, {"note": "prepare_due_drafts ran"})
                beat("send_queue", db, {"note": "drain ran"})
            except Exception as e:
                logging.error(f"heartbeat failed: {e}")

            try:
                result = _relearn_patterns(db)
                logging.info(f"re-learned patterns from real outcomes: {result}")
            except Exception as e:
                logging.error(f"relearn failed: {e}")

            db.close()
        except Exception as e:
            logging.error(f"worker cycle failed: {e}")
        time.sleep(CYCLE_SEC)
