"""
Auto-Warm background worker — runs in its OWN process.

Each cycle syncs replies, reconciles contact trust from evidence already on
record (a maintenance sweep, on its own slower cadence), updates conversation
memory, classifies leads, and
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.observability import setup_logging  # noqa: E402

# Replaces the previous basicConfig. Same stdout stream so pm2 still captures
# it, plus a rotating file under backend/logs — pm2's copy is what survives a
# crash, the local file is what exists when pm2 is not running, which after a
# reboot is the normal state.
setup_logging("worker")

if __name__ == "__main__":
    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401  — fail-closed EMAIL_SENT proof

    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()

    cycle_sec = int(os.getenv("SMART_OUTREACH_CYCLE_SECONDS", "600"))
    # Default OFF: production must opt in after /health + controlled send proof.
    # Autonomous outreach has a single production executor: purity-outreach
    # (smart_outreach_worker.py). Keep this worker for warm/enrichment,
    # reply-sync, trust maintenance, sequence preparation and learning only.
    # SMART_OUTREACH_ENABLED is retained as a compatibility flag but MUST NOT
    # grant this worker outbound authority; this prevents duplicate executors.
    limit = max(1, min(100, int(os.getenv("SMART_OUTREACH_LIMIT", "20"))))
    logging.info("Adaptive outreach executor: retired in purity-worker; limit=%s cycle=%ss", limit, cycle_sec)

    # Scrapling is an OPTIONAL web-intelligence dependency. It is deliberately
    # disabled by default and has no outbound-channel authority.
    scrapling_enabled = os.getenv("SCRAPLING_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    scrapling_hours = max(1.0, float(os.getenv("SCRAPLING_CYCLE_HOURS", "24")))
    scrapling_limit = max(1, min(100, int(os.getenv("SCRAPLING_LIMIT", "20"))))
    last_scrapling = None
    scrapling_note = "disabled"
    logging.info(
        "Scrapling web enrichment: enabled=%s every=%sh limit=%s",
        scrapling_enabled, scrapling_hours, scrapling_limit,
    )

    # Trust reconciliation is a MAINTENANCE job, not a decision engine. It reads
    # evidence already on record, reconciles it into email_trust, and writes a
    # TRUST_TRANSITION audit event. It never sends, never classifies a lead,
    # never sets cadence or offers, and never touches learning weights.
    #
    # Nightly by default: evaluate() is a fixpoint, so re-running it every
    # 10-minute outreach cycle re-derives identical states and buys nothing but
    # MX lookups. Set TRUST_SWEEP_HOURS=0 to disable.
    sweep_hours = max(0.0, float(os.getenv("TRUST_SWEEP_HOURS", "24")))
    last_sweep = None          # None => run once on the first cycle after start
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

            # Ordered after reply sync and before outreach on purpose: a reply
            # synced this cycle is fresh evidence, and a lead promoted here
            # becomes visible to evaluate_next_action() in the same cycle
            # instead of waiting for the next one.
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
                        # last_sweep deliberately NOT advanced: a failed sweep
                        # retries next cycle rather than silently skipping a day.
                        sweep_note = f"failed: {e}"[:120]
                        logging.error("trust sweep failed: %s", e)

            # Web intelligence is deliberately before outreach so newly found
            # first-party contact evidence can be evaluated by the existing
            # trust/outreach gates on a later cycle. Scrapling itself never sends.
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
                        # Do not advance last_scrapling after a failed run: the
                        # next worker cycle retries rather than silently skipping.
                        scrapling_note = f"failed: {e}"[:160]
                        logging.error("Scrapling enrichment failed: %s", e)
            else:
                scrapling_note = "disabled"

            # Intentionally no autonomous outreach here.
            # The dedicated purity-outreach process is the sole send executor.
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
                        "smart_outreach": "retired_in_worker",
                        "trust_sweep": sweep_note,
                        "scrapling": scrapling_note,
                    },
                )
                beat(
                    "smart_outreach",
                    db,
                    {
                        "note": "classify -> evaluate_next_action -> execute -> learn",
                        "limit": limit,
                        "enabled": False,
                        "scrapling_enabled": scrapling_enabled,
                    },
                )
            except Exception as e:
                logging.error("heartbeat failed: %s", e)
        except Exception as e:
            logging.error("worker cycle failed: %s", e)
        finally:
            db.close()
        time.sleep(cycle_sec)