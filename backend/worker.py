"""
Auto-Warm + autonomous outreach worker — runs in its OWN process.

Each cycle syncs replies, reconciles contact trust, updates conversation
memory, classifies leads, and — only when AUTO_OUTREACH_ENABLED is explicitly
on — executes due consent-safe automatic email outreach and learns measured
category/channel reply rates.

Ordinary outreach requires no founder approval. Commercial pricing/discount
policy remains governed by its existing gates.

Default: AUTO_OUTREACH_ENABLED=0 so merge ≠ live sending.
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.observability import setup_logging  # noqa: E402

setup_logging("worker")

if __name__ == "__main__":
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401

    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()

    cycle_sec = int(os.getenv("AUTO_OUTREACH_CYCLE_SECONDS", "600"))
    enabled = os.getenv("AUTO_OUTREACH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    limit = max(1, min(100, int(os.getenv("AUTO_OUTREACH_LIMIT", "20"))))
    logging.info("Autonomous outreach: enabled=%s limit=%s cycle=%ss", enabled, limit, cycle_sec)

    scrapling_enabled = os.getenv("SCRAPLING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    scrapling_hours = max(1.0, float(os.getenv("SCRAPLING_CYCLE_HOURS", "24")))
    scrapling_limit = max(1, min(100, int(os.getenv("SCRAPLING_LIMIT", "20"))))
    last_scrapling = None
    scrapling_note = "disabled"
    logging.info(
        "Scrapling web enrichment: enabled=%s every=%sh limit=%s",
        scrapling_enabled, scrapling_hours, scrapling_limit,
    )

    sweep_hours = max(0.0, float(os.getenv("TRUST_SWEEP_HOURS", "24")))
    last_sweep = None
    sweep_note = "not yet run"
    logging.info("trust sweep: every %sh (0=off)", sweep_hours)

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

            if sweep_hours:
                _now = time.monotonic()
                if last_sweep is None or (_now - last_sweep) >= sweep_hours * 3600:
                    try:
                        from app.services.trust_promoter import run as trust_sweep
                        sw = trust_sweep(db)
                        last_sweep = _now
                        sweep_note = f"moved={sw.get('moved')} of {sw.get('considered')} {sw.get('into')}"
                        logging.info(
                            "trust sweep: considered=%s moved=%s into=%s errors=%s",
                            sw.get("considered"), sw.get("moved"),
                            sw.get("into"), len(sw.get("errors") or []),
                        )
                    except Exception as e:
                        sweep_note = f"failed: {e}"[:120]
                        logging.error("trust sweep failed: %s", e)

            if scrapling_enabled:
                _now = time.monotonic()
                if last_scrapling is None or (_now - last_scrapling) >= scrapling_hours * 3600:
                    try:
                        from app.services.scrapling_harvester import harvest as scrapling_harvest
                        web_result = scrapling_harvest(db, limit=scrapling_limit, only_missing=True)
                        last_scrapling = _now
                        scrapling_note = (
                            f"processed={web_result.get('processed')} "
                            f"found={web_result.get('found')}"
                        )
                        logging.info("Scrapling enrichment: %s", scrapling_note)
                    except Exception as e:
                        scrapling_note = f"failed: {e}"[:160]
                        logging.error("Scrapling enrichment failed: %s", e)
            else:
                scrapling_note = "disabled"

            if enabled:
                try:
                    from app.services.outreach_lifecycle import run_automatic_cycle
                    result = run_automatic_cycle(db, limit=limit)
                    logging.info(
                        "autonomous outreach: processed=%s memory=%s learning=%s",
                        result.get("processed"),
                        result.get("memory"),
                        result.get("learning"),
                    )
                except Exception as e:
                    logging.error("autonomous outreach cycle failed: %s", e)
            else:
                logging.info("autonomous outreach disabled (set AUTO_OUTREACH_ENABLED=1 after runtime gate)")

            try:
                from app.services.sequence_engine import prepare_due_drafts
                prep = prepare_due_drafts(db)
                if prep.get("created") or prep.get("due"):
                    logging.info(
                        "legacy sequence drafts: created=%s due=%s skipped=%s",
                        prep.get("created"), prep.get("due"), prep.get("skipped"),
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
                beat("worker", db, {
                    "reply_sync": str(rep)[:120],
                    "autonomous_outreach": "enabled" if enabled else "disabled",
                    "trust_sweep": sweep_note,
                    "scrapling": scrapling_note,
                })
                beat("smart_outreach", db, {
                    "note": "classify -> evaluate_next_action -> execute -> learn",
                    "limit": limit,
                    "enabled": enabled,
                    "scrapling_enabled": scrapling_enabled,
                })
            except Exception as e:
                logging.error("heartbeat failed: %s", e)
        except Exception as e:
            logging.error("worker cycle failed: %s", e)
        finally:
            db.close()
        time.sleep(cycle_sec)
