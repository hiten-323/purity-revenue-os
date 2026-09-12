import re
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.models import B2BLead, CallHistory
from app.services.founder_call_pipeline import CALLABLE_SEGMENTS as _CALLABLE_SEGMENTS


class CallingAgentService:
    AI_CALLING_VERSION = "1.0-RC1"
    AI_CALLING_FROZEN = True
    ARCHITECTURE_STATUS = "RC1_APPROVED"
    CALLING_ENGINE_ENABLED = True
    VOICE = "hi-IN-MadhurNeural"
    DEFAULT_REALIZATION_PER_KG = 1400.0
    AUTO_COLD_CALLING = False
    AUTO_SAMPLE_DISPATCH = False
    AUTO_ORDER_BOOKING = False
    FOUNDER_APPROVAL_REQUIRED = True
    DNC_ENABLED = True
    MAX_CALL_ATTEMPTS = 3
    COOLDOWN_DAYS = 14
    CALL_CAMPAIGN_MARGIN_TARGET = 500000.0
    MAX_CALLS_PER_DAY = 25
    MAX_CALL_COST_PER_DAY = 500.0
    FOUNDER_OVERRIDE_ENABLED = True
    LEAD_LOCK_DURATION_MINUTES = 30
    CALL_ALLOWED_IF = ["IMPLIED_B2B", "EXPLICIT"]
    CALLABLE_SEGMENTS = _CALLABLE_SEGMENTS

    @staticmethod
    def normalize_company(company_name: str) -> str:
        if not company_name:
            return ""
        name = company_name.lower().strip()
        suffixes = [
            r"\bpvt\b", r"\bltd\b", r"\bprivate\b", r"\blimited\b",
            r"\bcorp\b", r"\bcorporation\b", r"\bllc\b", r"\bco\b",
            r"\bcompany\b", r"\binc\b", r"\bincorporated\b",
        ]
        for suffix in suffixes:
            name = re.sub(suffix, "", name)
        name = re.sub(r"[^\w\s]", "", name)
        return " ".join(name.split())

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
        today = datetime.utcnow().strftime("%Y-%m-%d")
        calls_count = db.query(func.count(CallHistory.id)).filter(
            func.strftime("%Y-%m-%d", CallHistory.call_date) == today
        ).scalar() or 0
        cost_sum = db.query(func.sum(CallHistory.cost)).filter(
            func.strftime("%Y-%m-%d", CallHistory.call_date) == today
        ).scalar() or 0.0
        return int(calls_count), float(cost_sum)

    @staticmethod
    def check_eligibility(
        db,
        lead: B2BLead,
        ignore_dnc_override: bool = False,
        cold_qualification: bool = False,
    ) -> tuple[bool, str]:
        """Apply the common call gates before any provider-side execution."""
        if not CallingAgentService.CALLING_ENGINE_ENABLED:
            return False, "calling_engine_disabled"

        calls_today, cost_today = CallingAgentService.check_daily_limits(db)
        if calls_today >= CallingAgentService.MAX_CALLS_PER_DAY:
            return False, "daily_call_limit_reached"
        if cost_today >= CallingAgentService.MAX_CALL_COST_PER_DAY:
            return False, "daily_cost_limit_reached"

        if not lead.phone:
            return False, "missing_phone"

        lead_div = (lead.division or "").lower().strip()
        if lead_div not in CallingAgentService.CALLABLE_SEGMENTS:
            return False, "invalid_segment"

        suggested_margin = lead.proposal_suggested_margin or 70.0
        margin = lead.estimated_value * suggested_margin / 100.0
        if margin <= 25000:
            return False, "low_margin"

        if CallingAgentService.DNC_ENABLED and lead.do_not_call:
            if not (CallingAgentService.FOUNDER_OVERRIDE_ENABLED and lead.dnc_override_by):
                return False, "do_not_call_active"

        consent = (lead.consent_status or "UNKNOWN").upper()
        if not cold_qualification and consent not in CallingAgentService.CALL_ALLOWED_IF:
            return False, f"insufficient_consent ({consent})"

        if (lead.call_attempts or 0) >= CallingAgentService.MAX_CALL_ATTEMPTS:
            return False, "max_call_attempts_reached"

        if lead.last_call_date:
            cooldown_limit = datetime.utcnow() - timedelta(days=CallingAgentService.COOLDOWN_DAYS)
            if lead.last_call_date > cooldown_limit:
                return False, "cooldown_active"

        if lead.lead_locked_until:
            if lead.lead_locked_until > datetime.utcnow():
                return False, "lead_locked"
            lead.lead_locked_until = None
            db.commit()

        norm_company = CallingAgentService.normalize_company(lead.company)
        lead.company_normalized = norm_company
        db.commit()
        dup_exists = db.query(B2BLead).filter(
            B2BLead.company_normalized == norm_company,
            B2BLead.id != lead.id,
            B2BLead.status.notin_(["COLD", "ARCHIVED"]),
        ).first()
        if dup_exists:
            return False, f"duplicate_company_active ({dup_exists.company})"

        return True, "eligible"

    @staticmethod
    def _place_qualification_call(db, lead: B2BLead, pipeline) -> tuple[bool, str]:
        """Place the disclosed cold qualification call through the sole voice authority."""
        from app.services import voice_router

        result = voice_router.place_call(
            lead,
            context={
                "opening": pipeline.OPENING_DISCLOSURE,
                "questions": list(pipeline.QUALIFICATION_QUESTIONS),
            },
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
    def _place_consented_call(db, lead: B2BLead) -> tuple[bool, str]:
        """Place an already-consented call through voice_router; never call a provider directly."""
        from app.services import voice_router

        result = voice_router.place_call(
            lead,
            context={"purpose": "consented_followup"},
        )
        if not result.placed:
            return False, f"provider_refused: {result.error}"

        lead.lead_owner = "AI Agent"
        lead.lead_locked_until = datetime.utcnow() + timedelta(
            minutes=CallingAgentService.LEAD_LOCK_DURATION_MINUTES
        )
        lead.call_attempts = (lead.call_attempts or 0) + 1
        lead.last_call_date = datetime.utcnow()
        lead.call_status = "CALLING"
        lead.call_provider = voice_router.active()
        db.commit()
        return True, f"consented_call_placed: {result.provider_call_id}"

    @staticmethod
    def trigger_vapi_call(db, lead: B2BLead) -> tuple[bool, str]:
        """Backward-compatible entry point; all voice execution is routed centrally.

        The historical name is retained for callers that have not migrated yet.
        It has no VAPI implementation and cannot select a provider itself.
        """
        from app.services import founder_call_pipeline as pipeline

        consent = (lead.consent_status or "UNKNOWN").upper()
        is_cold = consent not in CallingAgentService.CALL_ALLOWED_IF

        eligible, reason = CallingAgentService.check_eligibility(
            db, lead, cold_qualification=is_cold
        )
        if not eligible:
            return False, reason

        from app.services import voice_router
        cfg_ok, cfg_reason = voice_router.config_status()
        if not cfg_ok:
            return False, f"voice_not_configured: {cfg_reason}"

        if is_cold:
            may_call, why = pipeline.may_place_ai_call(lead)
            if not may_call:
                return False, f"cold_call_refused: {why}"
            if pipeline.daily_budget_remaining(db) <= 0:
                return False, (
                    f"daily_ai_call_cap_reached: {pipeline.MAX_AI_CALLS_PER_DAY} placed in the last 24h"
                )
            return CallingAgentService._place_qualification_call(db, lead, pipeline)

        return CallingAgentService._place_consented_call(db, lead)

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
            margin = lead.estimated_value * (lead.proposal_suggested_margin or 70.0) / 100.0
            return score, margin

        leads.sort(key=sort_key, reverse=True)
        campaign_leads = []
        cumulative_margin = 0.0
        estimated_cost = 0.0
        for lead in leads:
            suggested_margin = lead.proposal_suggested_margin or 70.0
            margin = lead.estimated_value * suggested_margin / 100.0
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
