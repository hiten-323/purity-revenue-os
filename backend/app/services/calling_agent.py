from datetime import datetime, timedelta
from app.models.models import B2BLead
from app.services.founder_call_pipeline import (
    CALLABLE_SEGMENTS as _CALLABLE_SEGMENTS,
    MAX_AI_CALL_ATTEMPTS_PER_LEAD as _MAX_ATTEMPTS,
    normalize_company as _normalize_company,
)


class CallingAgentService:
    AI_CALLING_VERSION = "1.0-RC1"
    AI_CALLING_FROZEN = True
    ARCHITECTURE_STATUS = "RC1_APPROVED"
    CALLING_ENGINE_ENABLED = True
    VOICE = "hi-IN-MadhurNeural"
    DEFAULT_REALIZATION_PER_KG = 1400.0
    AUTO_SAMPLE_DISPATCH = False
    AUTO_ORDER_BOOKING = False
    DNC_ENABLED = True
    # The pipeline owns the attempt budget and the retry cooldown now
    # (MAX_AI_CALL_ATTEMPTS_PER_LEAD, 72h window scheduling). These aliases
    # keep historical callers (e.g. endpoints.py objection handling) on the
    # same number without a second copy of the policy.
    MAX_CALL_ATTEMPTS = _MAX_ATTEMPTS
    CALL_CAMPAIGN_MARGIN_TARGET = 500000.0
    FOUNDER_OVERRIDE_ENABLED = True
    CALLABLE_SEGMENTS = _CALLABLE_SEGMENTS

    @staticmethod
    def normalize_company(company_name: str) -> str:
        return _normalize_company(company_name)

    @staticmethod
    def get_average_realization(db) -> float:
        try:
            skus = ["Purista", "Purica", "Ultra Blend", "Bold"]
            from app.models.models import Product
            products = db.query(Product).all()
            per_kg_prices = []
            for product in products:
                name = (product.product_name or "").lower()
                if not any(sku.lower() in name for sku in skus):
                    continue
                price = product.selling_price or 0.0
                if price <= 0:
                    continue
                if "50g" in name or "50" in name:
                    per_kg_prices.append(price * 20)
                elif "100g" in name or "100" in name:
                    per_kg_prices.append(price * 10)
                elif price < 500:
                    per_kg_prices.append(price * 15)
                else:
                    per_kg_prices.append(price)
            if per_kg_prices:
                return sum(per_kg_prices) / len(per_kg_prices)
        except Exception as exc:
            print(f"Error calculating average realization: {exc}")
        return CallingAgentService.DEFAULT_REALIZATION_PER_KG

    @staticmethod
    def check_eligibility(
        db,
        lead: B2BLead,
        ignore_dnc_override: bool = False,
    ) -> tuple[bool, str]:
        """Process-wide arming + the one calling authority.

        Per-lead permission is founder_call_pipeline.may_place_ai_call.
        This function adds the switches and the dial window that are not
        properties of a lead. There is no fleet-wide volume ceiling: inside
        the business-hours window every eligible lead is processed, bounded
        only by the per-lead gates (max 3 attempts, cooldown, calendar day).

        low_margin is deliberately gone. estimated_value is 0 on most cafes,
        so a 'safety' floor of Rs25k refused the priority segment without
        anyone deciding that. Commercial ranking belongs in campaign
        preview, not in the dial gate.
        """
        if not CallingAgentService.CALLING_ENGINE_ENABLED:
            return False, "calling_engine_disabled"

        from app.services import voice_router
        if voice_router.kill_switch_engaged():
            return False, "AI_CALLING_KILL_SWITCH is engaged -- no outbound AI calls"
        if not voice_router.calling_switched_on():
            return False, "AI_CALLING_ENABLED is not set to 1 -- no outbound AI calls"

        from app.services import founder_call_pipeline as pipeline
        hours_ok, hours_why = pipeline.call_hours_open(lead=lead)
        if not hours_ok:
            from app.observability import REFUSED, decision
            decision("ai_call.business_hours", REFUSED,
                     "dispatch window closed; nothing is reserved or dialled",
                     lead=lead)
            return False, f"business_hours_closed: {hours_why}"

        may_call, why = pipeline.may_place_ai_call(lead)
        if not may_call:
            return False, why
        return True, why

    @staticmethod
    def _place_qualification_call(db, lead: B2BLead, pipeline, scheduled_at=None,
                                  dry_run: bool = False) -> tuple[bool, str]:
        """Place the disclosed cold qualification call through the sole voice authority.

        The attempt is RESERVED (one atomic ledger row) before the network
        dial and committed — the reservation is durable, so a crash between
        reserve and dispatch can never re-dial or lose the attribution — then
        either marked dispatched on success or released on a provider refusal
        that never rang: release is for KNOWN not-answered states only.
        """
        from app.services import voice_quality
        from app.services import voice_router
        from app.services.outreach_learning import build_learning_context

        learning = build_learning_context(db, lead)
        questions = list(pipeline.QUALIFICATION_QUESTIONS)
        learned_question = learning.get("recommended_call_question")
        if learned_question and learned_question not in questions:
            questions.append(learned_question)

        # Call learning loop: standing do-not-call rules and the pre-dial
        # brief (prior calls with this lead + segment lessons). May only
        # REFUSE; a brief that cannot be built never blocks the dial.
        from app.services.call_intelligence.dial_hooks import after_dial, prepare_dial
        refusal, brief_ctx, call_brief, call_ref = prepare_dial(db, lead)
        if refusal:
            return False, f"cold_call_refused: {refusal}"

        reserved, reserve_reason = pipeline.begin_ai_attempt(db, lead)
        if not reserved:
            return False, reserve_reason

        opener_variant = (call_brief or {}).get("opener_variant")
        chosen_opening = pipeline.opening_for(lead, opener_variant=opener_variant)
        ok_disc, why_disc = pipeline.script_discloses(chosen_opening)
        if not ok_disc:
            # Safety: never dial a non-disclosing experiment line; fall back to baseline.
            chosen_opening = pipeline.opening_for(lead)
        result = voice_router.place_call(
            lead,
            db=db,
            context={

                "opening": chosen_opening,
                "questions": questions,
                "learning": {
                    "summary": learning.get("summary", ""),
                    "recommended_question": learned_question or "",
                    "top_objection": learning.get("top_objection"),
                    "evidence_count": learning.get("evidence_count", 0),
                },
                # Commercial CALL_CONSTRAINTS + conversational voice-quality
                # rules. One list in the payload so the sidecar cannot drop
                # either set by reading a second field that does not exist.
                "constraints": voice_quality.constraints_for_dispatch(
                    pipeline.CALL_CONSTRAINTS
                ),
                "handoff_topics": list(pipeline.HANDOFF_TOPICS),
                # From the record only, so "how did you get my number?" has a
                # true answer and the agent never improvises one.
                **pipeline.call_context(lead, db),
                **brief_ctx,
            },
            scheduled_at=scheduled_at,
            dry_run=dry_run,
        )
        if not result.placed:
            pipeline.release_ai_call_attempt(db, lead, reason=result.error)
            db.commit()
            return False, f"provider_refused: {result.error}"

        # Persist exactly what cross-lead experience influenced this call.
        # This is audit/memory, not a permission decision.
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id,
            event_type="AI_CALL_LEARNING_CONTEXT",
            actor="SMART_OUTREACH",
            channel="call",
            payload={
                "summary": learning.get("summary", ""),
                "recommended_question": learned_question or "",
                "top_objection": learning.get("top_objection"),
                "evidence_count": learning.get("evidence_count", 0),
                "category": learning.get("category"),
            },
            occurred_at=datetime.utcnow(),
        ))

        pipeline.mark_ai_call_dispatched(lead, db)
        lead.call_status = "CALLING"
        lead.call_provider = voice_router.active()
        db.commit()
        # Opened only after the lead state is durable, in its own commit, so
        # a learning-side failure can never roll back the dial bookkeeping.
        after_dial(db, lead, call_ref=call_ref, brief=call_brief,
                   provider=voice_router.active(),
                   provider_call_id=result.provider_call_id)
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return True, f"qualification_call_placed: {result.provider_call_id}"

    @staticmethod
    def trigger_vapi_call(db, lead: B2BLead, scheduled_at=None,
                          dry_run: bool = False) -> tuple[bool, str]:
        """Backward-compatible entry point; all voice execution is routed centrally.

        The historical name is retained for callers that have not migrated yet.
        It has no VAPI implementation and cannot select a provider itself.

        scheduled_at: see voice_router.place_call -- an explicit override of
        the provider's normal dispatch delay, for an operator-requested
        immediate test call. None (the default) changes nothing for every
        existing caller, including Smart Outreach.

        dry_run: pass through to voice_router.place_call so the whole stack
        (eligibility, reservation, ledger) can be exercised without a real
        dial. Provider placement is then reported, not performed.
        """
        from app.services import founder_call_pipeline as pipeline
        from app.services import voice_router

        eligible, reason = CallingAgentService.check_eligibility(db, lead)
        if not eligible:
            if (reason.startswith("AI_CALLING_")
                    or reason.startswith("business_hours_closed")
                    or reason == "calling_engine_disabled"):
                return False, reason
            return False, f"cold_call_refused: {reason}"

        cfg_ok, cfg_reason = voice_router.config_status()
        if not cfg_ok:
            return False, f"voice_not_configured: {cfg_reason}"

        from app.services.outbound_cohort import call_refusal
        refused = call_refusal(lead, db)
        if refused:
            return False, "cold_call_refused: " + refused
        return CallingAgentService._place_qualification_call(
            db, lead, pipeline, scheduled_at=scheduled_at, dry_run=dry_run)

    @staticmethod
    def get_campaign_preview(db) -> tuple[list[B2BLead], float, float]:
        leads = db.query(B2BLead).filter(
            B2BLead.division.in_(CallingAgentService.CALLABLE_SEGMENTS),
            B2BLead.lead_tier.in_(["A", "B"]),
            B2BLead.phone.isnot(None),
            B2BLead.do_not_call == False,
            (B2BLead.call_attempts == 0) | (B2BLead.call_attempts == None),
        ).all()

        def sort_key(lead):
            score = lead.lead_temperature_score or 0.0
            margin = (lead.estimated_value or 0.0) * (lead.proposal_suggested_margin or 70.0) / 100.0
            return score, margin

        leads.sort(key=sort_key, reverse=True)
        campaign_leads = []
        cumulative_margin = 0.0
        estimated_cost = 0.0
        for lead in leads:
            suggested_margin = lead.proposal_suggested_margin or 70.0
            margin = (lead.estimated_value or 0.0) * suggested_margin / 100.0
            campaign_leads.append(lead)
            cumulative_margin += margin
            estimated_cost += 20.0
            if cumulative_margin >= CallingAgentService.CALL_CAMPAIGN_MARGIN_TARGET:
                break
        return campaign_leads, cumulative_margin, estimated_cost

    @staticmethod
    def trigger_proposal_rescue_campaign(db) -> list[dict]:
        """Trigger outbound calls through the centralized voice authority."""
        cutoff = datetime.utcnow() - timedelta(days=7)
        stagnant_leads = db.query(B2BLead).filter(
            B2BLead.status == "PROPOSAL_SENT",
            (B2BLead.stage_entered_date <= cutoff)
            | ((B2BLead.stage_entered_date == None) & (B2BLead.last_updated <= cutoff)),
        ).all()

        triggered = []
        for lead in stagnant_leads:
            if lead.last_call_date and lead.last_call_date > cutoff:
                continue
            success, reason = CallingAgentService.trigger_vapi_call(db, lead)
            triggered.append({
                "lead_id": lead.id,
                "company": lead.company,
                "success": success,
                "reason": reason,
            })
        return triggered
