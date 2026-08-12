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

    # Periodically pull REAL replies from Zoho IMAP, then re-learn from the
    # outcomes. Both run here, not in the API, so this costs the dashboard
    # nothing. Reply detection is what moves engaged/won off zero — without it
    # the system can send but never learn whether anything landed.
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

            # Drain the approved send queue. The founder approves once; the
            # pacing is not their job. Before this, a batch that hit the
            # hourly cap simply stopped and 21 approved emails waited for
            # someone to re-run a script by hand.
            try:
                from app.services.send_queue import drain
                q = drain(db)
                if q["approved_waiting"]:
                    logging.info(f"send queue: {q['sent']} sent, "
                                 f"{q['held']} held, {q['blocked']} blocked, "
                                 f"{q['approved_waiting']} approved waiting")
            except Exception as e:
                logging.error(f"send queue drain failed: {e}")

            # Beat AFTER the work, never before. A heartbeat at the top of
            # the loop only proves the loop started — which is the useless
            # signal pm2 already provides.
            try:
                from app.services.heartbeat import beat
                beat("worker", db, {"reply_sync": str(rep)[:120]})
            except Exception as e:
                logging.error(f"heartbeat failed: {e}")

            result = _relearn_patterns(db)
            logging.info(f"re-learned patterns from real outcomes: {result}")
            db.close()
        except Exception as e:
            logging.error(f"worker cycle failed: {e}")
        time.sleep(CYCLE_SEC)
