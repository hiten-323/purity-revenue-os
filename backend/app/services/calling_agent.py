from datetime import datetime, timedelta
from app.models.models import B2BLead
from app.services.founder_call_pipeline import (
    CALLABLE_SEGMENTS as _CALLABLE_SEGMENTS,
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
    MAX_CALL_ATTEMPTS = 3
    COOLDOWN_DAYS = 14
    CALL_CAMPAIGN_MARGIN_TARGET = 500000.0
    MAX_CALLS_PER_DAY = 25
    MAX_CALL_COST_PER_DAY = 500.0
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
    def check_daily_limits(db) -> tuple[int, float]:
        """Delegate to the pipeline's 24h counter.

        The previous implementation used SQLite's strftime() against
        CallHistory.call_date, which is a no-op (or an error) on Neon
        Postgres — so the 'daily cap' silently did not cap in production.
        One counter, the one may_place_ai_call's caller already uses.
        """
        from app.services.founder_call_pipeline import calls_placed_today
        return calls_placed_today(db), 0.0

    @staticmethod
    def check_eligibility(
        db,
        lead: B2BLead,
        ignore_dnc_override: bool = False,
    ) -> tuple[bool, str]:
        """Process-wide arming + the one calling authority.

        Per-lead permission is founder_call_pipeline.may_place_ai_call.
        This function adds the switches and the daily cap that are not
        properties of a lead.

        low_margin is deliberately gone. estimated_value is 0 on most cafes,
        so a 'safety' floor of ₹25k refused the priority segment without
        anyone deciding that. Commercial ranking belongs in campaign
        preview, not in the dial gate.
        """
        if not CallingAgentService.CALLING_ENGINE_ENABLED:
            return False, "calling_engine_disabled"

        from app.services import voice_router
        if voice_router.kill_switch_engaged():
            return False, "AI_CALLING_KILL_SWITCH is engaged — no outbound AI calls"
        if not voice_router.calling_switched_on():
            return False, "AI_CALLING_ENABLED is not set to 1 — no outbound AI calls"

        from app.services import founder_call_pipeline as pipeline
        if pipeline.daily_budget_remaining(db) <= 0:
            return False, (
                f"daily_ai_call_cap_reached: {pipeline.MAX_AI_CALLS_PER_DAY} placed in the last 24h"
            )

        may_call, why = pipeline.may_place_ai_call(lead)
        if not may_call:
            return False, why
        return True, why

    @staticmethod
    def _place_qualification_call(db, lead: B2BLead, pipeline, scheduled_at=None) -> tuple[bool, str]:
        """Place the disclosed cold qualification call through the sole voice authority."""
        from app.services import voice_router

        result = voice_router.place_call(
            lead,
            context={
                "opening": pipeline.opening_for(lead),
                "questions": list(pipeline.QUALIFICATION_QUESTIONS),
                # Without these the model invents prices. Verified, not feared.
                "constraints": list(pipeline.CALL_CONSTRAINTS),
                "handoff_topics": list(pipeline.HANDOFF_TOPICS),
                # From the record only, so "how did you get my number?" has a
                # true answer and the agent never improvises one.
                **pipeline.call_context(lead, db),
            },
            scheduled_at=scheduled_at,
        )
        if not result.placed:
            return False, f"provider_refused: {result.error}"

        pipeline.advance(
            lead,
            db,
            pipeline.AI_CALL_ATTEMPTED,
            note="disclosed AI qualification call placed",
        )
        lead.ai_call_count = (lead.ai_call_count or 0) + 1
        lead.call_status = "CALLING"
        lead.call_provider = voice_router.active()
        lead.last_call_date = datetime.utcnow()
        db.commit()
        return True, f"qualification_call_placed: {result.provider_call_id}"

    @staticmethod
    def trigger_vapi_call(db, lead: B2BLead, scheduled_at=None) -> tuple[bool, str]:
        """Backward-compatible entry point; all voice execution is routed centrally.

        The historical name is retained for callers that have not migrated yet.
        It has no VAPI implementation and cannot select a provider itself.

        scheduled_at: see voice_router.place_call — an explicit override of
        the provider's normal dispatch delay, for an operator-requested
        immediate test call. None (the default) changes nothing for every
        existing caller, including Smart Outreach.
        """
        from app.services import founder_call_pipeline as pipeline
        from app.services import voice_router

        eligible, reason = CallingAgentService.check_eligibility(db, lead)
        if not eligible:
            if reason.startswith("AI_CALLING_") or reason.startswith("daily_") or reason == "calling_engine_disabled":
                return False, reason
            return False, f"cold_call_refused: {reason}"

        cfg_ok, cfg_reason = voice_router.config_status()
        if not cfg_ok:
            return False, f"voice_not_configured: {cfg_reason}"

        # Every AI call is a cold qualification call, including for a lead with
        # consent_status EXPLICIT. That consent is only ever recorded for email
        # or WhatsApp (the AI-call WhatsApp opt-in, an inbound WhatsApp reply,
        # the call sheet's "OK to email/WhatsApp?"). It used to unlock a
        # "consented call" path that skipped the DND registry scrub and the
        # one-call rule. Nothing records consent to be called, so no lead
        # bypasses may_place_ai_call — check_eligibility already ran it.
        return CallingAgentService._place_qualification_call(db, lead, pipeline, scheduled_at=scheduled_at)

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
