"""
Auto-Warm background worker — runs in its OWN process.

Why separate from the API:

The enrichment loop scrapes 9 sources per lead and parses large HTML documents.
That parsing is pure-Python CPU work, so it holds the GIL. While it ran as a
daemon thread inside the API process it periodically froze uvicorn's event loop —
even a trivial no-DB endpoint stalled ~1.8s and /b2b/kpis spiked to 12s, which
surfaced as random request timeouts and "Offline" flapping in the dashboard.

Running it as its own pm2 process isolates that CPU work completely: the API
event loop stays responsive, and the worker writes to SQLite (WAL mode) where a
writer no longer blocks readers.

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
    # Import inside main so the module-level FastAPI app is not constructed here.
    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()   # spawns its daemon loop thread in THIS process

    # Periodically pull REAL replies from Zoho IMAP, prepare due drafts, drain
    # approved sends, then re-learn from outcomes. All run here, not in the API.
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

            # Prepare due sequence drafts. Founder approves; nothing sends here.
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

            # Drain the approved send queue. The founder approves once; the
            # pacing is not their job.
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

            # Beat AFTER the work, never before.
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
