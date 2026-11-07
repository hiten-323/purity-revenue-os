r"""The worker's optional AI-call phase: candidate selector + one cycle.

This is the ONLY place that dials on a schedule. It is a separate, explicit
arm (AUTO_AI_CALLING_ENABLED), entirely independent of AI_CALLING_ENABLED —
so the production worker can run with AI_CALLING_ENABLED=1 (the gate every
dial path checks) while the scheduler STILL never dials until the founder
turns on the arm. That decoupling is the whole point: wiring the worker to
the scheduler must be a deliberate step, not a side effect of flipping the
per-call switch.

Whatever selects candidates here, the dial itself still goes through
CallingAgentService.trigger_vapi_call and therefore through every gate
(arming, business hours, may_place_ai_call). The selector is a queue; it is
not permission. There is NO fleet-wide volume ceiling — every eligible
candidate is dialled inside the business-hours window, bounded only by the
per-lead gates (max 3 attempts, cooldown, calendar day).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from sqlalchemy import or_, and_, case, func

from app.models.models import B2BLead
from app.services.founder_call_pipeline import (
    AI_NO_ANSWER,
    ELIGIBLE,
    AI_CALL_RETRY_COOLDOWN_HOURS,
)

AUTO_AI_CALLING_ENABLED_ENV = "AUTO_AI_CALLING_ENABLED"

# Festive outreach priority (founder change Oct 7 2026 16:36 IST): category-first.
# distributors/wholesalers -> supermarkets/modern trade -> hotels -> caterers ->
# corporates/pantry -> cafes/restaurants -> kirana. Mirrors festive_priority.py
# (which is the primary sort key in select_call_candidates and email due_now).
# Kirana/untagged rows whose NAME says wholesale/stockist/distributor rank 1;
# supermarket/hypermarket/supermart/mart rank 2.
_LOWER_NAME = func.lower(func.coalesce(B2BLead.company, ""))
_KIRANA_OR_UNTAGGED = or_(
    B2BLead.searched_category.in_(["retail_kirana", "kirana_store", "grocery", "retail", ""]),
    B2BLead.searched_category.is_(None),
)
PRIORITY_SEGMENT_CASE = case(
    (or_(
        B2BLead.searched_category.in_(["distributor", "wholesaler", "wholesale", "stockist", "super_stockist", "wholesaler_agglo", "cash_and_carry"]),
        B2BLead.segment.in_(["distributor", "wholesaler", "stockist", "wholesaler_agglo"]),
        B2BLead.division.in_(["distributor", "wholesaler", "stockist"]),
        and_(_KIRANA_OR_UNTAGGED, or_(
            _LOWER_NAME.like("%wholesale%"), _LOWER_NAME.like("%stockist%"),
            _LOWER_NAME.like("%distribut%"), _LOWER_NAME.like("%cash & carry%"),
            _LOWER_NAME.like("%cash and carry%"), _LOWER_NAME.like("%cash n carry%"),
        )),
    ), 1),
    (or_(
        B2BLead.searched_category.in_(["supermarket", "supermart", "hypermarket", "modern_trade", "grocery_chain", "retail_chain"]),
        B2BLead.segment.in_(["supermarket", "modern_trade", "retail_chain"]),
        B2BLead.division.in_(["supermarket", "modern_trade", "grocery_chain"]),
        and_(_KIRANA_OR_UNTAGGED, or_(
            _LOWER_NAME.like("%supermarket%"), _LOWER_NAME.like("%super market%"),
            _LOWER_NAME.like("%hypermarket%"), _LOWER_NAME.like("%hyper market%"),
            _LOWER_NAME.like("%supermart%"), _LOWER_NAME.like("%super mart%"),
            _LOWER_NAME.like("% mart"), _LOWER_NAME.like("% mart %"), _LOWER_NAME.like("mart %"),
        )),
    ), 2),
    (or_(
        B2BLead.searched_category.in_(["hotel", "resort", "guest_house", "hotel_canteen"]),
        B2BLead.segment.in_(["hotel", "hotel_canteen", "guest_house"]),
        B2BLead.division.in_(["hotel", "resort", "guest_house", "hotel_canteen"]),
    ), 3),
    (or_(
        B2BLead.searched_category.in_(["caterer", "catering", "catering_contractor", "event_catering", "banquet", "banquet_hall"]),
        B2BLead.segment.in_(["catering_contractor", "event_catering"]),
        B2BLead.division.in_(["catering", "caterer", "catering_contractor", "banquet"]),
    ), 4),
    (or_(
        B2BLead.searched_category.in_(["corporate_office", "corporate", "office_pantry", "facility_management", "institutional_buyer", "manufacturing", "corporate_gifting", "gifting", "hamper", "festive_gifting"]),
        B2BLead.segment.in_(["corporate", "corporate_office", "gifting", "corporate_gifting", "hamper", "festive_gifting", "facility_management"]),
        B2BLead.division.in_(["corporate_office", "office_pantry", "facility_management", "manufacturing", "corporate_gifting", "gifting", "hamper", "festive_gifting"]),
    ), 5),
    (or_(
        B2BLead.searched_category.in_(["cafe", "restaurant", "horeca", "dhaba"]),
        B2BLead.segment.in_(["horeca", "cafe", "restaurant"]),
        B2BLead.division.in_(["cafe", "restaurant", "horeca"]),
    ), 6),
    (or_(
        B2BLead.searched_category.in_(["retail_kirana", "kirana_store", "grocery", "retail"]),
        B2BLead.segment.in_(["grocery", "kirana_store", "retail"]),
        B2BLead.division.in_(["retail_kirana", "retail"]),
    ), 7),
    else_=9
)

# Opportunity & Intent Triggers:
# Tier 1: High Buying Intent & Vendor Switching (Festive Hamper Curators, active switch candidates with current supplier/brand, Hot/High Intent Score >= 70)
# Tier 2: Category Expansion & Resellers (Marts/Supermarkets adding gourmet coffee, Distributors expanding portfolio, Warm Intent Score >= 40)
# Tier 3: General pool
BUYING_INTENT_CASE = case(
    (or_(
        (B2BLead.current_supplier.isnot(None) & (B2BLead.current_supplier != "")),
        (B2BLead.current_brand.isnot(None) & (B2BLead.current_brand != "")),
        B2BLead.intent_tier.in_(["Hot", "High"]),
        (B2BLead.intent_score >= 70),
        B2BLead.segment.in_(["gifting", "corporate_gifting", "hamper", "festive_gifting"]),
        B2BLead.division.in_(["gifting", "corporate_gifting", "hamper", "festive_gifting"]),
    ), 1),
    (or_(
        B2BLead.intent_tier == "Warm",
        (B2BLead.intent_score >= 40),
        (B2BLead.coffee_buying_score >= 70),
        B2BLead.segment.in_(["distributor", "wholesaler", "stockist", "supermarket", "modern_trade"]),
        B2BLead.division.in_(["distributor", "wholesaler", "stockist", "supermarket", "modern_trade"]),
    ), 2),
    else_=3
)


def armed() -> bool:
    """The scheduler arm. Default OFF — scheduling dials is deliberate."""
    return (os.getenv(AUTO_AI_CALLING_ENABLED_ENV, "0") or "0").strip().lower() in (
        "1", "true", "yes", "on")


def select_call_candidates(db, limit: int | None = None) -> list:
    """The dial queue for one cycle, prioritized by urgency, commercial tier and permission:

    Priority order:
      1. Explicit requested callbacks due now (lead requested exact time / 15-min retry) — highest priority;
      2. Retry-eligible AI_NO_ANSWER leads (cooldown passed) — prioritized by commercial tier;
      3. Never-called ELIGIBLE cold leads — prioritized by festive category (distributors/wholesalers -> supermarkets/modern trade -> hotels -> caterers -> corporates -> cafes/restaurants -> kirana; founder change 7 Oct 16:36 IST).

    Explicit requested callbacks always take precedence over general retries and cold leads.
    """
    from app.services import identity

    now = datetime.utcnow()

    from app.services.outbound_cohort import allow_list_ids, call_refusal
    allowed_ids = sorted(allow_list_ids()) or [-1]
    base_dialable_filter = (
        B2BLead.id.in_(allowed_ids),
        (B2BLead.do_not_call.is_(False) | B2BLead.do_not_call.is_(None)),
        B2BLead.phone.isnot(None),
        B2BLead.phone != "",
        # SHARED_QUARANTINE (6 Oct, reversible): phone is on record for 5+ other
        # leads; excluded from the AI CALL queue only. Email is unaffected.
        # 3 Nov 2026 (Hiten "pause, fix, resume"): QUARANTINED (shared-directory
        # phone) leads are skipped too; before this only SHARED_QUARANTINE was.
        B2BLead.phone_trust.is_(None) | B2BLead.phone_trust.notin_(["INVALID", "SHARED_QUARANTINE", "QUARANTINED", "SIP_INVALID", "DEAD_SERIES"]),
        (B2BLead.ai_call_count < 3) | B2BLead.ai_call_count.is_(None),
        # 7 Oct 2026: DISQUALIFIED (not a fit) / CLOSED_LOST / DO_NOT_CONTACT rows and
        # opted-out contacts never enter the dial queue (status alone used to be ignored).
        B2BLead.status.is_(None) | B2BLead.status.notin_(["DISQUALIFIED", "CLOSED_LOST", "DO_NOT_CONTACT"]),
        B2BLead.contact_status.is_(None) | B2BLead.contact_status.notin_(["OPTED_OUT", "DO_NOT_CONTACT"]),
    )

    # 1. Explicit requested callbacks (highest priority)
    explicit_cb_q = (
        db.query(B2BLead)
        .filter(
            *base_dialable_filter,
            or_(
                B2BLead.outreach_stage.in_([AI_NO_ANSWER, "AI_CALLBACK_REQUESTED", "FOUNDER_CALL_REQUESTED", "AI_CALL_ATTEMPTED"]),
                B2BLead.call_outcome_last.in_(["CALLBACK_REQUESTED", "HUMAN_HANDOFF"]),
            ),
            or_(
                B2BLead.founder_callback_window.isnot(None),
                (B2BLead.callback_retry_count.isnot(None) & (B2BLead.callback_retry_count > 0)),
            ),
            or_(
                (B2BLead.ai_retry_after.isnot(None) & (B2BLead.ai_retry_after <= now)),
                B2BLead.ai_retry_after.is_(None),
            ),
        )
        .order_by(B2BLead.ai_retry_after.asc().nullsfirst(), PRIORITY_SEGMENT_CASE.asc())
    )
    raw_explicit = explicit_cb_q.all()
    # 3 Nov 2026 fix 3 (Hiten 11:49 IST): unproven scraped +91 6-series numbers
    # are held out of the queue until a free check proves them.
    from app.services.call_intelligence.six_series_hold import hold_reason as _six_hold

    def _held(l):
        try:
            return bool(_six_hold(db, l))
        except Exception:  # noqa: BLE001
            return False
    explicit_cb_leads = [l for l in raw_explicit if not identity.is_fabricated_pattern(l.phone)
                     and identity.is_voice_dialable(l.phone) and not _held(l)]  # 27 Oct: mobiles + geographic landlines (voice rule)
    if limit is not None:
        explicit_cb_leads = explicit_cb_leads[:limit]
    explicit_ids = {l.id for l in explicit_cb_leads}

    remaining_limit = (limit - len(explicit_cb_leads)) if limit is not None else None

    # 2. General retries (Daily rotation, prioritized by Commercial Tier)
    retry_leads = []
    if remaining_limit is None or remaining_limit > 0:
        retry_q = (
            db.query(B2BLead)
            .filter(
                *base_dialable_filter,
                B2BLead.outreach_stage == AI_NO_ANSWER,
                ~B2BLead.id.in_(explicit_ids) if explicit_ids else True,
                or_(
                    (B2BLead.ai_retry_after.isnot(None) & (B2BLead.ai_retry_after <= now)),
                    (B2BLead.ai_retry_after.is_(None) & B2BLead.last_call_date.isnot(None) & (B2BLead.last_call_date <= now - timedelta(hours=AI_CALL_RETRY_COOLDOWN_HOURS))),
                ),
            )
            .order_by(
                B2BLead.ai_retry_after.asc().nullslast(),
                PRIORITY_SEGMENT_CASE.asc(),
                B2BLead.last_call_date.asc().nullslast()
            )
        )
        raw_retries = retry_q.all()
        from app.services.founder_call_pipeline import classify_meaningful_conversation
        retry_leads = [
            l for l in raw_retries
            if not identity.is_fabricated_pattern(l.phone)
            and identity.is_voice_dialable(l.phone)
            and not _held(l)
            and not (l.call_outcome_last and classify_meaningful_conversation(l.call_outcome_last, transcript=l.call_transcript or ""))
        ]


    # 3. Cold eligible leads (Festive category priority: distributors/wholesalers -> supermarkets/modern trade -> hotels -> caterers -> corporates/pantry -> cafes/restaurants -> kirana)
    cold_leads = []
    if remaining_limit is None or remaining_limit > 0:
        cold_q = (
            db.query(B2BLead)
            .filter(
                *base_dialable_filter,
                or_(B2BLead.outreach_stage == ELIGIBLE, B2BLead.outreach_stage.is_(None)),
                or_(B2BLead.ai_call_count == 0, B2BLead.ai_call_count.is_(None)),
                B2BLead.last_call_date.is_(None),
            )
            .order_by(
                BUYING_INTENT_CASE.asc(),
                PRIORITY_SEGMENT_CASE.asc(),
                B2BLead.intent_score.desc().nullslast(),
                B2BLead.coffee_buying_score.desc().nullslast(),
                B2BLead.id.desc(),
            )
        )
        raw_cold = cold_q.all()
        cold_leads = [l for l in raw_cold if not identity.is_fabricated_pattern(l.phone)
                     and identity.is_voice_dialable(l.phone) and not _held(l)]  # 27 Oct: mobiles + geographic landlines (voice rule)

    # FESTIVE-FIRST MERGE (founder-approved 6 Oct): festive category rank is the
    # primary key across due retries AND cold leads (distributors/wholesalers ->
    # supermarkets/modern trade -> hotels -> caterers -> corporates/pantry ->
    # cafes/restaurants -> kirana; founder change 7 Oct 16:36 IST). Within a
    # category: due retries first (earliest retry time, existing order), then cold
    # leads (existing intent/segment order). Retry spacing is untouched: only
    # retries already past ai_retry_after / cooldown are in retry_leads.
    # Explicit requested callbacks stay ahead of everything (time commitments).
    # INSTANT-COFFEE FIT (8 Oct): within a festive rank, CONFIRMED before LIKELY
    # (coffee_fit_rank 0 < 1), then due retries before cold, then existing order.
    from app.services.festive_priority import (
        festive_rank as _festive_rank,
        coffee_fit_rank as _coffee_fit_rank,
        category_focus_rank as _cat_focus,
    )
    # 14 Oct: +91 6xxxx numbers were rejected by the carrier 21 of 29 times
    # (1 connect) - dial them after other numbers in the same rank, not never.
    def _risk(l):
        d = "".join(ch for ch in (l.phone or "") if ch.isdigit())
        return 1 if (len(d) == 12 and d[2] == "6") else 0
    # 14 Oct FIT GATE (founder-approved): instant-coffee fit tier is now the
    # PRIMARY key - CONFIRMED/LIKELY first, unassessed next, UNLIKELY last.
    # Rollback: AI_CALL_FIT_GATE=0 (then the old festive-first order applies).
    from app.services.festive_priority import fit_gate_rank as _fit_gate
    pool = [((_fit_gate(l), _cat_focus(l), _festive_rank(l), _risk(l), _coffee_fit_rank(l), 0, i), l) for i, l in enumerate(retry_leads)]
    pool += [((_fit_gate(l), _cat_focus(l), _festive_rank(l), _risk(l), _coffee_fit_rank(l), 1, i), l) for i, l in enumerate(cold_leads)]
    pool.sort(key=lambda kv: kv[0])
    merged = [l for _, l in pool]
    if remaining_limit is not None:
        merged = merged[:max(0, remaining_limit)]
    chosen = explicit_cb_leads + merged
    return [lead for lead in chosen if call_refusal(lead, db) is None]


def run_cycle(db, limit: int | None = None, dry_run: bool = False) -> dict:
    """Dial every eligible candidate in the queue.

    Every candidate is re-checked at dial time (trigger_vapi_call runs the
    full eligibility stack), so a candidate who became ineligible between
    selection and dispatch is refused, not dialled. There is no fleet-wide
    capacity check to stop on — the cycle ends when the queue has no
    remaining candidates (or the per-lead gates refuse them). dry_run passes
    through to the provider so this can operate as a tripwire (verifying the
    ledger shape, arming, and candidate ordering with zero real calls).
    """
    from app.observability import REFUSED, decision
    from app.services.calling_agent import CallingAgentService

    if not armed():
        decision("ai_call.cycle", REFUSED,
                 "%s is not set to 1 — the scheduler does not dial"
                 % AUTO_AI_CALLING_ENABLED_ENV)
        return {"armed": False, "considered": 0, "dialed": 0, "refused": []}

    # Fleet-wide volume cap (2026-10-03 incident): at most
    # AI_CALL_CYCLE_MAX_DIALS per cycle and AI_CALL_MAX_INFLIGHT outstanding.
    # Only ever reduces volume; a cap that cannot be computed dials nothing.
    try:
        from app.services.call_intelligence.volume import dial_budget
        budget = dial_budget(db, limit=limit)
    except Exception as exc:
        decision("ai_call.cycle", REFUSED,
                 "volume cap unavailable (%s) — not dialling" % exc.__class__.__name__)
        return {"armed": True, "considered": 0, "dialed": 0, "refused": [],
                "budget": 0}
    if budget <= 0:
        decision("ai_call.cycle", REFUSED,
                 "volume cap: no dial budget this cycle (in-flight limit reached or cap 0)")
        return {"armed": True, "considered": 0, "dialed": 0, "refused": [],
                "budget": 0}

    # 13 Oct calling window (founder-approved): connects are best 11:30-17:00 IST
    # (22/87 midday, 22/70 afternoon vs 5/36 morning). Outside the window only
    # a fraction of the budget is used. Business-hours gate is unchanged.
    try:
        _ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
        _m = _ist.hour * 60 + _ist.minute
        _lo, _hi = (int(x) for x in os.getenv("AI_CALL_PEAK_WINDOW_IST_MIN", "690-1020").split("-"))
        if not (_lo <= _m < _hi):
            _frac = float(os.getenv("AI_CALL_OFFPEAK_FRACTION", "0.4"))
            budget = max(1, int(budget * _frac)) if _frac > 0 else 0
            if budget <= 0:
                return {"armed": True, "considered": 0, "dialed": 0, "refused": [], "budget": 0}
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services.call_intelligence.catalogue_followup import materialize_due_followups
        if materialize_due_followups(db):
            db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()

    considered = 0
    dialed = 0
    refused = []
    # 2026-10-06 fix: selecting exactly `budget` candidates let two
    # permanently-refused leads (shared phone on 5+ leads) occupy the whole
    # window every cycle -> 0 dials. Look further down the queue (bounded),
    # skip per-lead refusals, and still never dial more than `budget`.
    # Process-wide gates (business hours / arming / engine off) end the cycle.
    scan_window = max(budget, budget * int(os.getenv("AI_CALL_SCAN_MULTIPLIER", "50")))
    for lead in select_call_candidates(db, limit=scan_window):
        if dialed >= budget:
            break
        from app.services.supplier_guard import is_supplier as _is_sup
        if _is_sup(lead)[0]:
            refused.append({"lead_id": lead.id, "reason": "supplier/vendor"})
            continue
        from app.services.outbound_cohort import call_refusal
        refused_why = call_refusal(lead, db)
        if refused_why:
            refused.append({"lead_id": lead.id, "reason": refused_why})
            continue
        considered += 1
        ok, reason = CallingAgentService.trigger_vapi_call(
            db, lead, dry_run=dry_run)
        if ok:
            dialed += 1
            decision("ai_call.cycle", "ALLOWED",
                     "dispatched via scheduler", lead=lead)
        else:
            refused.append({"lead_id": lead.id, "reason": reason})
            r = str(reason or "")
            if (r.startswith("AI_CALLING_") or r.startswith("business_hours_closed")
                    or r == "calling_engine_disabled"
                    or r.startswith("voice_not_configured")):
                db.expire_all()
                break
        db.expire_all()

    decision("ai_call.cycle", "DONE",
             "considered=%d dialed=%d refused=%d" % (considered, dialed, len(refused)))
    return {"armed": True, "considered": considered, "dialed": dialed,
            "refused": refused, "budget": budget}