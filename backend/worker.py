"""
Auto-Warm + autonomous outreach worker — runs in its OWN process.

Each cycle syncs replies, reconciles Klaviyo WhatsApp delivery events and contact
trust, updates conversation memory, classifies leads, and — only when
AUTO_OUTREACH_ENABLED / SMART_OUTREACH_ENABLED is explicitly on — executes due
consent-safe automatic outreach, drains approved queues, runs scheduled AI call
cycles (when AUTO_AI_CALLING_ENABLED=1), and learns measured category/channel
reply rates.

Ordinary outreach requires no founder approval. Commercial pricing/discount
policy remains governed by its existing gates.

Default: AUTO_OUTREACH_ENABLED=0 / SMART_OUTREACH_ENABLED=0 so merge != live sending.
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.observability import setup_logging  # noqa: E402

setup_logging("worker")


def _flag_on(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _outreach_enabled() -> bool:
    return _flag_on("AUTO_OUTREACH_ENABLED") or _flag_on("SMART_OUTREACH_ENABLED")


if __name__ == "__main__":
    import app.models.models  # noqa: F401
    try:
        import app.models.send_proof_fix  # noqa: F401
    except ImportError:
        pass

    from app.api.endpoints import start_auto_warm_worker, _relearn_patterns
    from app.database.database import SessionLocal

    logging.info("Auto-Warm worker starting (separate process)")
    start_auto_warm_worker()

    cycle_sec = int(os.getenv("AUTO_OUTREACH_CYCLE_SECONDS") or os.getenv("SMART_OUTREACH_CYCLE_SECONDS", "600"))
    enabled = _outreach_enabled()
    limit = max(1, min(100, int(os.getenv("AUTO_OUTREACH_LIMIT") or os.getenv("SMART_OUTREACH_LIMIT", "20"))))
    logging.info("Autonomous outreach: enabled=%s limit=%s cycle=%ss", enabled, limit, cycle_sec)

    scrapling_enabled = _flag_on("SCRAPLING_ENABLED", "0")
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
        enabled = _outreach_enabled()
        db = SessionLocal()
        try:
            try:
                from app.api.endpoints import sync_email_replies
                rep = sync_email_replies(days=7, db=db)
                logging.info("reply sync: %s", rep)
            except Exception as e:
                logging.error("reply sync failed: %s", e)
                rep = {"error": str(e)}

            try:
                from app.services.klaviyo_sync import sync_klaviyo_delivery_events
                klav_rec = sync_klaviyo_delivery_events(db=db)
                if klav_rec.get("checked") or klav_rec.get("pending_provider_reconciliation"):
                    logging.info("klaviyo wa delivery sync: %s", klav_rec)
            except Exception as e:
                db.rollback()
                logging.error("klaviyo wa delivery sync failed: %s", e)

            # Ordered after reply sync and before outreach on purpose: a reply
            # synced this cycle is fresh evidence, and a lead promoted here
            # becomes visible to evaluate_next_action() in the same cycle.
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

            _purge_min = float(os.getenv("DEAD_LEAD_PURGE_MINUTES", "60"))
            if _purge_min > 0:
                _now = time.monotonic()
                if (globals().get("_last_purge") is None
                        or (_now - globals()["_last_purge"]) >= _purge_min * 60):
                    try:
                        from app.services.crm_tracker import CRMTrackerService
                        n_p = CRMTrackerService.purge_dead_leads(db)
                        globals()["_last_purge"] = _now
                        logging.info("dead-lead purge: %s set DORMANT", n_p)
                    except Exception as e:
                        db.rollback()
                        logging.error("dead-lead purge failed: %s", e)

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

                try:
                    from app.models.models import EmailDraft, B2BLead
                    pending_drafts = db.query(EmailDraft).filter(
                        EmailDraft.status.in_(["DRAFT", "PENDING", "EDITED"])
                    ).all()
                    for draft in pending_drafts:
                        lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
                        if not lead or not (lead.email or "").strip():
                            draft.status = "BLOCKED_NO_EMAIL"
                    if pending_drafts:
                        db.commit()

                    from app.services import auto_send as _auto
                    if _auto.enabled():
                        _ar = _auto.evaluate_email(db, dry_run=False)
                        promoted = len(_ar["approved"])
                        _wr = _auto.evaluate_whatsapp(db, dry_run=False)
                        logging.info(
                            "auto_send: email approved=%s room=%s held_markers=%s blocked_content=%s blocked_lead=%s | wa approved=%s blocked=%s",
                            promoted, _ar["room"], len(_ar["held_markers"]), len(_ar["blocked_content"]),
                            len(_ar["blocked_lead"]), len(_wr["approved"]), len(_wr["blocked"]),
                        )
                except Exception as e:
                    db.rollback()
                    logging.error("draft auto-promotion failed: %s", e)

                try:
                    from app.services.send_queue import drain as drain_send_queue
                    drain_res = drain_send_queue(db)
                    if drain_res.get("sent") or drain_res.get("approved_waiting") or drain_res.get("blocked"):
                        logging.info("send queue drain: %s", drain_res)
                except Exception as e:
                    logging.error("send queue drain failed: %s", e)
            else:
                logging.info("autonomous outreach disabled (set AUTO_OUTREACH_ENABLED=1 after runtime gate)")

            ai_calling_armed = False
            ai_call_cycle_result = {}
            try:
                from app.services.ai_call_cycle import armed as _ai_calling_armed
                from app.services.ai_call_cycle import run_cycle as run_ai_call_cycle
                ai_calling_armed = bool(_ai_calling_armed())
                from app.services import human_pace as _hp
                if ai_calling_armed and not _hp.may_dial():
                    logging.info("ai call cycle: human_pace gap not elapsed, skipping")
                elif ai_calling_armed:
                    cycle = run_ai_call_cycle(db)
                    _hp.record_dial_attempt(bool(cycle.get("dialed")))
                    ai_call_cycle_result = {
                        "considered": cycle.get("considered", 0),
                        "dialed": cycle.get("dialed", 0),
                        "refused": len(cycle.get("refused") or []),
                    }
                    logging.info(
                        "ai call cycle: considered=%s dialed=%s refused=%s armed=1",
                        cycle.get("considered"), cycle.get("dialed"),
                        len(cycle.get("refused") or []),
                    )
                else:
                    logging.info("ai call cycle disabled (set AUTO_AI_CALLING_ENABLED=1 after approval)")
            except Exception as e:
                logging.error("ai call cycle failed: %s", e)

            try:
                from app.services.call_intelligence.reconciler import run_from_worker
                rec = run_from_worker(db)
                if rec.get("mode") != "off":
                    logging.info(
                        "call reconciler: %s",
                        {k: rec.get(k) for k in ("mode", "stuck_calling_leads", "proposed_terminal_state", "applied")},
                    )
            except Exception as e:
                db.rollback()
                logging.error("call reconciler failed: %s", e)

            try:
                from app.services.sequence_engine import prepare_due_drafts
                prep = prepare_due_drafts(db, auto_approve=enabled)
                if prep.get("created") or prep.get("due"):
                    logging.info(
                        "sequence drafts: created=%s due=%s skipped=%s",
                        prep.get("created"), prep.get("due"), prep.get("skipped"),
                    )
            except Exception as e:
                logging.error("sequence prepare failed: %s", e)

            try:
                from app.services import warm_followup
                wf = warm_followup.tick(db)
                if any(wf.get(k) for k in ("nudges", "parked", "reopened")):
                    logging.info("warm follow-up: %s", wf)
            except Exception as e:
                db.rollback()
                logging.error("warm follow-up tick failed: %s", e)

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
                    "ai_calling_armed": ai_calling_armed,
                    "ai_call_cycle": ai_call_cycle_result,
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

        _tick = int(os.getenv("AI_CALL_TICK_SECONDS", "0") or 0)
        if _tick > 0:
            _wake = time.monotonic() + cycle_sec
            while True:
                _left = _wake - time.monotonic()
                from app.services import human_pace as _hp
                _this_tick = _hp.call_tick_seconds(_tick)
                if _left <= _this_tick:
                    time.sleep(max(0.0, _left))
                    break
                time.sleep(_this_tick)
                _tdb = SessionLocal()
                try:
                    from app.services.ai_call_cycle import armed as _armed_t, run_cycle as _run_t
                    if _armed_t() and _hp.may_dial():
                        _c = _run_t(_tdb)
                        _hp.record_dial_attempt(bool(_c.get("dialed")))
                        if _c.get("dialed"):
                            logging.info(
                                "ai call tick: considered=%s dialed=%s refused=%s",
                                _c.get("considered"), _c.get("dialed"),
                                len(_c.get("refused") or []),
                            )
                except Exception as e:
                    _tdb.rollback()
                    logging.error("ai call tick failed: %s", e)
                finally:
                    _tdb.close()
        else:
            time.sleep(cycle_sec)
