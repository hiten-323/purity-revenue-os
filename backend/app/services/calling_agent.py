import os
import re
import urllib.request
import json
from datetime import datetime, timedelta
from sqlalchemy import func
from app.models.models import B2BLead, Product, CallHistory

class CallingAgentService:
    # RC1 Freeze Flags & Constants
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

    # Calibration Limits for first 30 days
    MAX_CALLS_PER_DAY = 25
    MAX_CALL_COST_PER_DAY = 500.0
    FOUNDER_OVERRIDE_ENABLED = True

    LEAD_LOCK_DURATION_MINUTES = 30
    CALL_ALLOWED_IF = ["IMPLIED_B2B", "EXPLICIT"]
    CALLABLE_SEGMENTS = ["corporate", "gifting", "distributor", "retail", "horeca"]

    @staticmethod
    def normalize_company(company_name: str) -> str:
        if not company_name:
            return ""
        name = company_name.lower().strip()
        # Remove common business suffixes
        suffixes = [
            r"\bpvt\b", r"\bltd\b", r"\bprivate\b", r"\blimited\b",
            r"\bcorp\b", r"\bcorporation\b", r"\bllc\b", r"\bco\b",
            r"\bcompany\b", r"\binc\b", r"\bincorporated\b"
        ]
        for suf in suffixes:
            name = re.sub(suf, "", name)
        name = re.sub(r"[^\w\s]", "", name)
        return " ".join(name.split())

    @staticmethod
    def get_average_realization(db) -> float:
        try:
            skus = ["Purista", "Purica", "Ultra Blend", "Bold"]
            from app.models.models import Product
            products = db.query(Product).all()
            per_kg_prices = []
            for p in products:
                name = (p.product_name or "").lower()
                matched = False
                for s in skus:
                    if s.lower() in name:
                        matched = True
                        break
                if matched:
                    price = p.selling_price or 0.0
                    if price > 0:
                        # Convert to per KG if it is a retail pack (50g or 100g)
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
        except Exception as e:
            print(f"Error calculating average realization: {e}")
        return CallingAgentService.DEFAULT_REALIZATION_PER_KG

    @staticmethod
    def check_daily_limits(db) -> tuple[int, float]:
        # Count calls today
        today = datetime.utcnow().strftime("%Y-%m-%d")
        calls_count = db.query(func.count(CallHistory.id)).filter(
            func.strftime("%Y-%m-%d", CallHistory.call_date) == today
        ).scalar() or 0
        # Sum costs today
        cost_sum = db.query(func.sum(CallHistory.cost)).filter(
            func.strftime("%Y-%m-%d", CallHistory.call_date) == today
        ).scalar() or 0.0
        return int(calls_count), float(cost_sum)

    @staticmethod
    def check_eligibility(db, lead: B2BLead, ignore_dnc_override: bool = False,
                          cold_qualification: bool = False) -> tuple[bool, str]:
        """Gates common to every call.

        cold_qualification=True skips ONLY the recorded-consent clause, because
        for that one disclosed introductory call the permission decision belongs
        to founder_call_pipeline.may_place_ai_call() — which applies a stricter
        set (once per lead, preference-registry scrubbed, disclosed as AI).
        Every other gate here still applies. Nothing else is relaxed.
        """
        if not CallingAgentService.CALLING_ENGINE_ENABLED:
            return False, "calling_engine_disabled"

        # Check daily limits
        calls_today, cost_today = CallingAgentService.check_daily_limits(db)
        if calls_today >= CallingAgentService.MAX_CALLS_PER_DAY:
            return False, "daily_call_limit_reached"
        if cost_today >= CallingAgentService.MAX_CALL_COST_PER_DAY:
            return False, "daily_cost_limit_reached"

        # Check basic fields
        if not lead.phone:
            return False, "missing_phone"
        
        # Check callable segment/division
        lead_div = (lead.division or "").lower().strip()
        if lead_div not in CallingAgentService.CALLABLE_SEGMENTS:
            return False, "invalid_segment"

        # Calculate Expected Margin
        suggested_margin = lead.proposal_suggested_margin or 70.0
        margin = lead.estimated_value * suggested_margin / 100.0
        if margin <= 25000:
            return False, "low_margin"

        # Check DNC (unless overridden by founder override flag)
        if CallingAgentService.DNC_ENABLED and lead.do_not_call:
            if not (CallingAgentService.FOUNDER_OVERRIDE_ENABLED and lead.dnc_override_by):
                return False, "do_not_call_active"

        # Check Consent Status
        consent = lead.consent_status or "UNKNOWN"
        if not cold_qualification and consent not in CallingAgentService.CALL_ALLOWED_IF:
            return False, f"insufficient_consent ({consent})"

        # Check call attempts
        attempts = lead.call_attempts or 0
        if attempts >= CallingAgentService.MAX_CALL_ATTEMPTS:
            return False, "max_call_attempts_reached"

        # Check Cooldown
        if lead.last_call_date:
            cooldown_limit = datetime.utcnow() - timedelta(days=CallingAgentService.COOLDOWN_DAYS)
            if lead.last_call_date > cooldown_limit:
                return False, "cooldown_active"

        # Check Lead Lock with 30 minute timeout recovery
        if lead.lead_locked_until:
            if lead.lead_locked_until > datetime.utcnow():
                return False, "lead_locked"
            else:
                # Stale lock recovery, auto-unlock
                lead.lead_locked_until = None
                db.commit()

        # Check Duplicate Company Protection
        norm_company = CallingAgentService.normalize_company(lead.company)
        lead.company_normalized = norm_company
        db.commit()
        
        dup_exists = db.query(B2BLead).filter(
            B2BLead.company_normalized == norm_company,
            B2BLead.id != lead.id,
            B2BLead.status.notin_(["COLD", "ARCHIVED"])
        ).first()
        if dup_exists:
            return False, f"duplicate_company_active ({dup_exists.company})"

        return True, "eligible"

    @staticmethod
    def _place_qualification_call(db, lead: B2BLead, pipeline) -> tuple[bool, str]:
        """The one disclosed AI call, dialled through the real provider adapter.

        Nothing is written until the provider says it accepted the call. An
        earlier version of this path locked the lead and burned an attempt
        first, so a provider outage consumed the single call this lead will
        ever get. Here a refusal costs nothing and the lead stays ELIGIBLE.
        """
        from app.services import voice_router

        result = voice_router.place_call(
            lead,
            context={
                # The disclosure and the questions come from the pipeline so
                # there is one script, and script_discloses() has vetted it.
                "opening": pipeline.OPENING_DISCLOSURE,
                "questions": list(pipeline.QUALIFICATION_QUESTIONS),
            },
        )
        if not result.placed:
            return False, f"provider_refused: {result.error}"

        # Attempted, not yet answered. The outcome arrives from the provider
        # webhook and goes through pipeline.record_ai_outcome(), which is the
        # only thing that may move this lead any further.
        pipeline.advance(lead, db, pipeline.AI_CALL_ATTEMPTED,
                         note="disclosed AI qualification call placed")
        lead.ai_call_count = (lead.ai_call_count or 0) + 1
        lead.call_status = "CALLING"
        lead.call_provider = voice_router.active()
        lead.last_call_date = datetime.utcnow()
        db.commit()
        return True, f"qualification_call_placed: {result.provider_call_id}"

    @staticmethod
    def trigger_vapi_call(db, lead: B2BLead) -> tuple[bool, str]:
        # Two different actions live behind this one function, and they answer
        # to different authorities:
        #
        #   a CONSENTED call — they replied, opted in, or asked for a callback.
        #                      check_eligibility's consent clause owns it.
        #   a COLD call      — nobody has agreed to anything yet. This is the
        #                      one disclosed AI qualification call, and
        #                      founder_call_pipeline owns it: once per lead,
        #                      preference-registry scrubbed, disclosed as AI.
        #
        # The path is chosen by what is RECORDED on the lead, not by which
        # endpoint called in, so a caller cannot pick the laxer route. Every
        # lead in the database is UNKNOWN today, so in practice every call
        # through here is currently the cold one.
        from app.services import founder_call_pipeline as pipeline
        is_cold = ((lead.consent_status or "UNKNOWN").upper()
                   not in CallingAgentService.CALL_ALLOWED_IF)

        eligible, reason = CallingAgentService.check_eligibility(
            db, lead, cold_qualification=is_cold)
        if not eligible:
            return False, reason

        # Config gate FIRST — before touching the lead. Without this, an
        # unconfigured Vapi still locked the lead, burned a call_attempt, and set
        # call_status=CALLING, only to 401 and land on FAILED. Repeatedly hitting
        # the call button with no key would exhaust MAX_CALL_ATTEMPTS on a lead
        # that was never actually dialled. Fail cleanly and mutate nothing.
        # Provider gate FIRST, before touching the lead. An unconfigured
        # provider used to still lock the lead, burn a call_attempt and set
        # call_status=CALLING before failing, so repeated attempts exhausted
        # MAX_CALL_ATTEMPTS on a lead that was never dialled. Fail cleanly and
        # mutate nothing.
        #
        # Voice goes through voice_router, which resolves the configured
        # provider (Nuraveda). Bolna over Exotel used to be the other option
        # and was removed — a second adapter nobody configures is how a config
        # gate ends up validating one provider while the code dials another.
        from app.services import voice_router
        _cfg_ok, _cfg_why = voice_router.config_status()
        if not _cfg_ok:
            return False, f"voice_not_configured: {_cfg_why}"

        if is_cold:
            _may, _why = pipeline.may_place_ai_call(lead)
            if not _may:
                return False, f"cold_call_refused: {_why}"
            if pipeline.daily_budget_remaining(db) <= 0:
                return False, (f"daily_ai_call_cap_reached: "
                               f"{pipeline.MAX_AI_CALLS_PER_DAY} placed in the "
                               f"last 24h")
            return CallingAgentService._place_qualification_call(db, lead, pipeline)

        # Consent is RECORDED, never defaulted.
        #
        # This block used to upgrade any UNKNOWN lead to IMPLIED_B2B, sourced to
        # "GOOGLE_MAPS" — i.e. the system granted itself permission on the
        # grounds that it had scraped the business. All 1,831 leads are UNKNOWN
        # with no consent_source, so every one of them would have been marked
        # consented by the act of attempting a call.
        #
        # It also defeated the gate above it: CALL_ALLOWED_IF is
        # ["IMPLIED_B2B", "EXPLICIT"], and this wrote IMPLIED_B2B moments before
        # that list was checked. The check could not fail.
        #
        # Worse, it escalated across channels. whatsapp_sender.CONSENT_OK
        # accepts IMPLIED_B2B, so one call attempt on a cold scraped lead would
        # have made that number WhatsApp-messageable under Meta's opt-in rules
        # without the buyer ever doing anything.
        #
        # Being listed on Google Maps is not consent to receive automated calls.
        # UNKNOWN stays UNKNOWN and the call is refused. Real consent comes from
        # something that actually happened — a reply, a WhatsApp opt-in, or a
        # founder call where they said yes, which is what log_call's
        # WHATSAPP_CONSENT outcome records.
        _consent = (lead.consent_status or "UNKNOWN").upper()
        if _consent not in CallingAgentService.CALL_ALLOWED_IF:
            return False, (
                f"no_consent_on_record: consent_status={_consent}. An automated "
                f"call needs recorded consent; scraping a listing is not consent. "
                f"Capture it on a founder call or an inbound reply first."
            )

        # Lock lead and increment attempts
        lead.lead_owner = "AI Agent"
        lead.lead_locked_until = datetime.utcnow() + timedelta(minutes=CallingAgentService.LEAD_LOCK_DURATION_MINUTES)
        lead.call_attempts = (lead.call_attempts or 0) + 1
        lead.last_call_date = datetime.utcnow()
        lead.call_status = "CALLING"
        db.commit()

        openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

        # These two were referenced below but never defined anywhere in this
        # module, so this path raised NameError at the `if vapi_phone_id:`
        # line — which sits OUTSIDE the try, so it escaped uncaught to the
        # three endpoints that call this. Reading them from the environment
        # turns a crash into a refusal.
        #
        # Note the deeper incoherence, left visible rather than papered over:
        # the config gate above validated BOLNA credentials, and this block
        # then dialled VAPI. Whichever provider was real, one of the two was
        # wrong. Bolna has since been removed entirely; the cold-call path goes
        # through voice_router (Nuraveda) instead;
        # this consented branch is unreachable today (consent is recorded on
        # zero leads) and should be migrated deliberately, not incidentally.
        vapi_key = os.getenv("VAPI_API_KEY", "").strip()
        vapi_phone_id = os.getenv("VAPI_PHONE_NUMBER_ID", "").strip()
        if not vapi_key:
            return False, ("voice_not_configured: VAPI_API_KEY is not set, and "
                           "the gate above validates the configured voice "
                           "provider — this consented-call path needs "
                           "migrating to voice_router.place_call()")

        # Format prompt
        system_prompt = (
            "You are Ravi from Purity Beans, speaking on behalf of Hiten Jain's sales team. "
            "Speak only Hindi (prioritize Hinglish style for natural-sounding conversations). "
            "Goal: Qualify warm B2B coffee leads for office pantry, corporate gifting, or wholesale distribution. "
            "Ask these questions naturally:\n"
            "1. Kya aap office pantry ya corporate gifting handle karte hain?\n"
            "2. Kya aap coffee products procure karte hain?\n"
            "3. Aapki organization mein monthly coffee consumption kitni hoti hai aur abhi kaunsi brand use karte hain?\n"
            "4. Vendor change ke decisions kaun approve karta hai aapki organization mein?\n"
            "5. Taste checking ke liye kya sample receive karna chahenge?\n\n"
            "If they are interested, collect: Name, Designation, Phone, WhatsApp, and Email.\n"
            "Conclude by saying: 'Main Hiten Jain ji ki team se hoon. Hum Purity Beans premium instant coffee provide karte hain. Main sample aur catalogue WhatsApp kar deta hoon.'"
        )

        vapi_payload = {
            "assistant": {
                "name": "Ravi from Purity Beans",
                "voice": {
                    "provider": "azure",
                    "voiceId": CallingAgentService.VOICE
                },
                "model": {
                    "provider": "custom-llm",
                    "url": "https://openrouter.ai/api/v1",
                    "key": openrouter_key,
                    "model": "qwen/qwen-2.5-72b-instruct",
                    "systemPrompt": system_prompt
                },
                "firstMessage": "Namaste sir, main Purity Beans se bol raha hoon. Kya aap corporate gifting, office pantry, ya hospitality procurement dekhte hain?"
            },
            "customer": {
                "number": lead.phone
            }
        }
        if vapi_phone_id:
            vapi_payload["phoneNumberId"] = vapi_phone_id

        # POST request to Vapi
        try:
            req = urllib.request.Request(
                "https://api.vapi.ai/call/phone",
                data=json.dumps(vapi_payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {vapi_key}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            # Send call trigger asynchronously (under 5 seconds timeout)
            with urllib.request.urlopen(req, timeout=5) as response:
                res_body = json.loads(response.read().decode("utf-8"))
                call_id = res_body.get("id")
                if call_id:
                    lead.vapi_call_id = call_id
                    db.commit()
                    return True, "calling_initiated"
                else:
                    raise Exception("Failed to retrieve Call ID from Vapi response.")
        except Exception as e:
            print(f"Error calling Vapi API: {e}")
            lead.call_status = "FAILED"
            lead.lead_locked_until = None
            db.commit()
            return False, f"api_error: {str(e)}"

    @staticmethod
    def get_campaign_preview(db) -> tuple[list[B2BLead], float, float]:
        # Query warm/hot leads of Tier A/B in callable segments
        leads = db.query(B2BLead).filter(
            B2BLead.division.in_(CallingAgentService.CALLABLE_SEGMENTS),
            B2BLead.lead_tier.in_(["A", "B"]),
            B2BLead.phone.isnot(None),
            B2BLead.do_not_call == False,
            (B2BLead.call_attempts == 0) | (B2BLead.call_attempts == None)
        ).all()

        # Sort leads by lead_temperature_score DESC, Action Priority Score DESC
        # Note: Expected Margin is: lead.estimated_value * suggested_margin / 100
        def sort_key(l):
            score = l.lead_temperature_score or 0.0
            # Calculate expected margin
            margin = l.estimated_value * (l.proposal_suggested_margin or 70.0) / 100.0
            return (score, margin)

        leads.sort(key=sort_key, reverse=True)

        campaign_leads = []
        cumulative_margin = 0.0
        estimated_cost = 0.0
        
        for l in leads:
            suggested_margin = l.proposal_suggested_margin or 70.0
            margin = l.estimated_value * suggested_margin / 100.0
            
            campaign_leads.append(l)
            cumulative_margin += margin
            estimated_cost += 20.0 # Estimate ₹20 per call
            
            if cumulative_margin >= CallingAgentService.CALL_CAMPAIGN_MARGIN_TARGET:
                break
                
        return campaign_leads, cumulative_margin, estimated_cost

    @staticmethod
    def trigger_proposal_rescue_campaign(db) -> list[dict]:
        """Triggers outbound AI calls for leads in PROPOSAL_SENT stagnant for 7+ days."""
        from datetime import datetime, timedelta
        from app.models.models import B2BLead
        
        cutoff = datetime.utcnow() - timedelta(days=7)
        stagnant_leads = db.query(B2BLead).filter(
            B2BLead.status == "PROPOSAL_SENT",
            (B2BLead.stage_entered_date <= cutoff) | ((B2BLead.stage_entered_date == None) & (B2BLead.last_updated <= cutoff))
        ).all()
        
        triggered = []
        for lead in stagnant_leads:
            # Check if recently called (cooldown check)
            if lead.last_call_date and lead.last_call_date > cutoff:
                continue
                
            success, reason = CallingAgentService.trigger_vapi_call(db, lead)
            triggered.append({
                "lead_id": lead.id,
                "company": lead.company,
                "success": success,
                "reason": reason
            })
            
        return triggered
