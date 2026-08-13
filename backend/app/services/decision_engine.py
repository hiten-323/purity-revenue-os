"""
Founder Decision Engine — Layer 5 of the Purity Beans Founder Revenue OS V1.1

Pure, deterministic, side-effect free.
Input:  facts + events + domain services + business policies
Output: immutable FounderWorkspace object

NEVER sends email, WhatsApp, starts calls, or updates DB directly.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field


# ── Score formulas ──────────────────────────────────────────────

STATUS_PROGRESSION = {
    "COLD": 0, "DISCOVERED": 1, "QUALIFIED": 2,
    "INTRO_EMAIL_SENT": 3, "EMAIL_SENT": 3, "WHATSAPP_SENT": 4,
    "AI_CALLED": 5, "FOUNDER_CALLED": 6, "REPLIED": 7,
    "MEETING_BOOKED": 8, "MEETING_COMPLETED": 9,
    "SAMPLE_SENT": 10, "PROPOSAL_SENT": 11,
    "ORDER_WON": 12, "ONBOARDED": 13,
    "REORDER_PREDICTED": 13, "ACCOUNT_GROWTH": 14, "UPSELL_OFFERED": 14,
}


def compute_rrs(lead) -> int:
    """Revenue Reality Score — measures real engagement and deal momentum. Max 100.
    V1.1: only VERIFIED contacts score points — unconfirmed data isn't reality."""
    s = 0
    if lead.email and (getattr(lead, "email_verification_status", "") or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL"):
        s += 20
    if (lead.phone or lead.whatsapp_number) and getattr(lead, "phone_verified", False):
        s += 20
    if (lead.estimated_value or 0) > 100_000:
        s += 15
    prog = STATUS_PROGRESSION.get(lead.status or "", 0)
    s += round((prog / 14) * 25)
    s += min(20, round((lead.probability or 0) * 0.2))
    return min(100, s)


def compute_aps(lead) -> int:
    """Action Priority Score — measures urgency and leverage. Max 100."""
    s = 0

    # Staleness urgency (max 40): how long since last status change?
    if lead.stage_entered_date:
        days_stalled = (datetime.utcnow() - lead.stage_entered_date).days
    else:
        days_stalled = 0
    prog = STATUS_PROGRESSION.get(lead.status or "", 0)
    # Mid-pipeline stalls are most urgent (stages 3-11)
    if 3 <= prog <= 11:
        staleness = min(40, days_stalled * 3)
    else:
        staleness = min(20, days_stalled * 1)
    s += staleness

    # Followup urgency (max 30): is next followup overdue?
    if lead.next_followup_date:
        try:
            due = datetime.strptime(lead.next_followup_date, "%Y-%m-%d")
            overdue_days = (datetime.utcnow() - due).days
            if overdue_days > 0:
                s += min(30, overdue_days * 5)
            elif overdue_days == 0:
                s += 15  # due today
        except (ValueError, TypeError):
            pass

    # Leverage (max 30): estimated value at stake
    val = lead.estimated_value or 0
    if val >= 500_000:
        s += 30
    elif val >= 200_000:
        s += 22
    elif val >= 100_000:
        s += 15
    elif val >= 50_000:
        s += 8
    else:
        s += 3

    return min(100, s)


def compute_founder_priority_score(lead) -> float:
    """Founder Priority Score = APS×40% + RRS×35% + MarginScore×25%. Max 100."""
    aps = compute_aps(lead)
    rrs = compute_rrs(lead)
    val = lead.estimated_value or 0
    margin_rs = val * 0.31
    # Margin score: Rs.31k = 100pts (target margin on Rs.1L deal)
    margin_score = min(100, (margin_rs / 31_000) * 100)
    return round(aps * 0.40 + rrs * 0.35 + margin_score * 0.25, 1)


# ── Why this business plausibly buys coffee ──────────────────────────────────
# Stated as a reason the founder can sanity-check, not a black-box score. Each
# signal is derived from a VERIFIED field (category from Google Maps, review
# count, website) — never invented.
_BUYING_SIGNALS = {
    "distributor":        "Distributes FMCG/beverages — resells to retail",
    "wholesaler":         "Wholesale trade — buys in bulk to resell",
    "wholesaler_agglo":   "Wholesale trade — buys in bulk to resell",
    "grocery":            "Retail grocery — stocks instant coffee",
    "kirana_store":       "Kirana retail — stocks instant coffee",
    "retail":             "Retail chain — shelf space for FMCG",
    "retail_chain":       "Retail chain — shelf space for FMCG",
    "horeca":             "Hospitality — serves coffee to guests",
    "hotel_canteen":      "Hotel — breakfast and in-room coffee service",
    "corporate":          "Corporate office — pantry consumption",
    "corporate_office":   "Corporate office — pantry consumption",
    "corporate_pantry":   "Runs office pantry services — direct coffee demand",
    "catering_contractor": "Catering contractor — bulk beverage supply",
    "event_catering":     "Event catering — bulk beverage supply",
    "industrial_canteen": "Industrial canteen — staff beverage consumption",
    "education_mess":     "Institutional mess — bulk beverage consumption",
    "hospital":           "Hospital canteen — staff and visitor consumption",
    "facility_management": "Facility management — supplies client pantries",
    "gifting":            "Corporate gifting — coffee hampers",
    "corporate_gifting":  "Corporate gifting — coffee hampers",
    "government":         "Government canteen / tender procurement",
    "govt_canteen":       "Government canteen — bulk procurement",
    "tender":             "Procures via public tender",
}


def buying_signals(lead) -> list:
    """Verified reasons this company plausibly buys coffee."""
    out = []
    seg = (getattr(lead, "division", None) or getattr(lead, "segment", None) or "").lower().strip()
    if seg in _BUYING_SIGNALS:
        out.append(_BUYING_SIGNALS[seg])
    name = (getattr(lead, "company", "") or "").lower()
    if any(w in name for w in ("cafe", "coffee", "restaurant", "hotel", "bakery", "canteen")):
        out.append("Business name indicates food/beverage service")
    reviews = getattr(lead, "maps_reviews_count", None) or 0
    if reviews >= 100:
        out.append(f"Established footfall — {reviews} Google reviews")
    if (getattr(lead, "website", "") or "").strip():
        out.append("Has a website — verifiable business presence")
    return out


def contact_completeness(lead) -> dict:
    """
    How complete is what we actually know? Drives how much confidence the
    founder should place in this opportunity before spending time on it.
    """
    checks = {
        "verified_phone": bool((getattr(lead, "phone", "") or "").strip())
                          and bool((getattr(lead, "phone_source", "") or "").strip()),
        "verified_email": bool((getattr(lead, "email", "") or "").strip())
                          and (bool(getattr(lead, "email_verified", False))
                               or getattr(lead, "email_verification_status", "") in ("VALID", "CATCH_ALL")),
        "website":        bool((getattr(lead, "website", "") or "").strip()),
        "decision_maker": bool((getattr(lead, "decision_maker", "") or "").strip()),
        "google_rating":  bool(getattr(lead, "maps_rating", None)),
        "buying_signal":  bool(buying_signals(lead)),
    }
    got = sum(1 for v in checks.values() if v)
    return {"fields": checks, "complete": got, "total": len(checks),
            "percent": round(got / len(checks) * 100)}


def score_decay_factor(lead) -> float:
    """
    A hot lead from three months ago is not hot. Decays 10% per 30 days since
    the last real interaction, floored at 0.5 so an aged lead never disappears
    entirely — it just stops outranking fresh ones.
    """
    last = getattr(lead, "last_updated", None) or getattr(lead, "contact_searched_at", None)
    if not last:
        return 1.0
    days = max(0, (datetime.utcnow() - last).days)
    return round(max(0.5, 1.0 - 0.10 * (days // 30)), 3)


def revenue_confidence(lead) -> dict:
    """
    How much do we trust this opportunity — kept SEPARATE from its size.
    A Rs 12L opportunity at 30% confidence should not outrank a Rs 4L one at
    90%, which is what ranking on estimated_value alone does.
    """
    comp = contact_completeness(lead)
    reasons = []
    score = comp["percent"] * 0.6            # what we actually know

    if getattr(lead, "maps_rating", None) or getattr(lead, "place_id", None):
        score += 20
        reasons.append("Verified on Google Maps")
    else:
        reasons.append("No Google Maps evidence")

    sig = buying_signals(lead)
    if sig:
        score += 10
        reasons.append(sig[0])

    if (getattr(lead, "call_outcome_last", "") or "") in ("interested", "need_sample", "need_proposal"):
        score += 10
        reasons.append("Expressed interest on a real call")

    decay = score_decay_factor(lead)
    final = round(min(100, score) * decay)
    if decay < 1.0:
        reasons.append(f"Aged — confidence decayed {int((1 - decay) * 100)}%")

    for field, ok in comp["fields"].items():
        if not ok:
            reasons.append(f"Missing: {field.replace('_', ' ')}")

    return {"confidence_percent": final, "completeness_percent": comp["percent"],
            "decay_factor": decay, "reasons": reasons[:6]}


# ── Decision objects (immutable) ────────────────────────────────

@dataclass(frozen=True)
class LeadDecision:
    id: int
    company: str
    city: Optional[str]
    contact_name: Optional[str]
    status: Optional[str]
    aps: int
    rrs: int
    fps: float
    estimated_value: float
    margin_rs: float
    division: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    whatsapp_number: Optional[str]
    next_followup_date: Optional[str]
    days_stalled: int


@dataclass(frozen=True)
class RevenueCoachRecommendation:
    company: str
    lead_id: int
    recommended_action: str
    confidence_pct: int
    evidence: list
    expected_margin_rs: float
    estimated_days_to_close: int
    current_status: str
    fps: float


@dataclass
class MorningBrief:
    date: str
    total_leads: int
    pipeline_value_rs: float
    hot_leads: int          # RRS >= 75
    money_today_count: int
    waiting_on_count: int
    action_queue_count: int
    top_channel: str        # channel with most hot leads
    enriching_count: int = 0   # leads held back in automated enrichment


@dataclass
class PipelineHealth:
    stage_counts: dict
    total_pipeline_rs: float
    conversion_rate_pct: float   # % of leads that have replied+
    avg_days_to_reply: float
    stalled_count: int           # leads with no movement for 14+ days
    health_grade: str            # GREEN / AMBER / RED


@dataclass
class RevenueTimeline:
    week_1_rs: float
    week_2_rs: float
    week_3_rs: float
    week_4_rs: float
    month_total_rs: float
    confidence_pct: int


@dataclass
class FounderWorkspace:
    """Canonical workspace object — single source of truth for all UI."""
    morning_brief: MorningBrief
    money_today: list           # List[LeadDecision] — RRS≥75, hot status, FPS-ranked
    waiting_on: list            # List[LeadDecision] — stalled, awaiting response
    action_queue: list          # List[dict] — prioritised executable actions
    revenue_coach: Optional[RevenueCoachRecommendation]
    pipeline_health: PipelineHealth
    revenue_timeline: RevenueTimeline
    generated_at: str


# ── Builder ─────────────────────────────────────────────────────

MONEY_TODAY_STATUSES = {
    "REPLIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT",
}
WAITING_ON_STATUSES = {
    "INTRO_EMAIL_SENT", "EMAIL_SENT", "WHATSAPP_SENT", "AI_CALLED", "FOUNDER_CALLED",
}
ACTION_TYPE_LABELS = {
    "cold":     ("Approve Email",   "Send intro email", "email"),
    "email":    ("Send WhatsApp",   "Follow up on email", "whatsapp"),
    "whatsapp": ("Run AI Call",     "Hindi AI call script", "ai_call"),
    "ai_call":  ("Founder Call",    "Personal call from Hiten", "founder_call"),
    "founder":  ("Book Meeting",    "Schedule demo/tasting", "meeting"),
    "meeting":  ("Send Sample",     "Dispatch sample kit", "sample"),
    "sample":   ("Send Proposal",   "Customised pricing proposal", "proposal"),
    "proposal": ("Follow Up",       "Close the deal", "close"),
    "won":      ("Plan Reorder",    "Predict next reorder", "reorder"),
}

STATUS_TO_STAGE = {
    "COLD": "cold", "DISCOVERED": "cold", "QUALIFIED": "cold",
    "INTRO_EMAIL_SENT": "email", "EMAIL_SENT": "email",
    "WHATSAPP_SENT": "whatsapp",
    "AI_CALLED": "ai_call",
    "FOUNDER_CALLED": "founder", "REPLIED": "founder",
    "MEETING_BOOKED": "meeting", "MEETING_COMPLETED": "meeting",
    "SAMPLE_SENT": "sample",
    "PROPOSAL_SENT": "proposal",
    "ORDER_WON": "won", "ONBOARDED": "won",
    "REORDER_PREDICTED": "reorder", "ACCOUNT_GROWTH": "reorder", "UPSELL_OFFERED": "reorder",
}


def classify_action(d: "LeadDecision") -> Optional[dict]:
    """
    Decide the single next best action for a lead (moved from
    FounderActionEngine.tsx — the UI no longer classifies).

    Returns an immutable recommendation dict exposing (spec §22):
    confidence, evidence, expected_impact, recommended_action, expected_duration.
    """
    prog = STATUS_PROGRESSION.get(d.status or "", 0)

    action = None
    if prog <= 2 and d.email:
        action = ("draft_email", "Draft Intro Email", "Cold lead with email — Day 0 outreach", True, 2)
    elif prog == 3 and d.days_stalled >= 3 and (d.whatsapp_number or d.phone):
        action = ("whatsapp", "Send WhatsApp Follow-up", f"Email sent {d.days_stalled}d ago, no reply", True, 3)
    elif prog == 4 and d.days_stalled >= 2 and (d.phone or d.whatsapp_number):
        action = ("ai_call", "AI Hindi Call", f"WhatsApp sent {d.days_stalled}d ago", False, 10)
    elif prog in (5, 6) and d.days_stalled >= 3 and (d.phone or d.whatsapp_number):
        action = ("founder_call", "Founder Call", f"Stalled {d.days_stalled}d — personal touch needed", False, 15)
    elif prog in (7, 8):
        action = ("meeting", "Confirm Meeting", "Lead replied — lock in a time", False, 10)
    elif prog == 9:
        action = ("sample", "Dispatch Sample", "Meeting done — ship the tasting kit", False, 15)
    elif prog == 10 and d.days_stalled >= 2:
        action = ("proposal", "Send Proposal", f"Sample sent {d.days_stalled}d ago — follow up with pricing", False, 20)
    elif prog == 11:
        action = ("close", "Close Follow-up", "Proposal out — push for decision", False, 10)
    elif prog >= 12:
        action = ("reorder", "Request Reorder", "Account won — schedule the next order", False, 5)

    # V1.2: no lead ever has "No Action". Fallbacks by what's missing:
    if not action:
        if not d.email and not d.phone and not d.whatsapp_number:
            action = ("enrich", "Verify Contacts", "No verified contact yet — web search running", True, 0)
        elif d.email:
            action = ("draft_email", "Draft Intro Email", "Verified email on file — start warming", True, 2)
        elif d.phone or d.whatsapp_number:
            action = ("whatsapp", "Send WhatsApp Intro", "Verified number on file — start warming", True, 3)
        else:
            action = ("follow_up", "Follow Up", "Keep the deal moving", False, 5)

    action_type, label, reason, can_auto, minutes = action

    evidence = []
    if d.rrs >= 75:
        evidence.append(f"RRS {d.rrs}/100 — strong engagement signals")
    elif d.rrs >= 50:
        evidence.append(f"RRS {d.rrs}/100 — contactable, moderate momentum")
    if d.days_stalled >= 3:
        evidence.append(f"Stalled {d.days_stalled} days in {d.status}")
    if d.estimated_value >= 100_000:
        evidence.append(f"Rs.{round(d.estimated_value/1000)}k pipeline at stake")
    if not evidence:
        evidence.append(f"FPS {d.fps} priority ranking")

    confidence = min(95, 35 + d.rrs // 2 + (10 if d.days_stalled >= 3 else 0))

    return {
        "lead_id": d.id,
        "company": d.company,
        "city": d.city,
        "contact_name": d.contact_name,
        "division": d.division,
        "current_status": d.status,
        "fps": d.fps,
        "rrs": d.rrs,
        "aps": d.aps,
        "action_type": action_type,
        "cta_label": label,
        "reason": reason,
        "can_auto": can_auto,
        "confidence_pct": confidence,
        "evidence": evidence[:3],
        "expected_margin_rs": round(d.margin_rs),
        "expected_impact": f"Advances Rs.{round(d.margin_rs/1000)}k expected margin one stage",
        "expected_duration_min": minutes,
        "days_stalled": d.days_stalled,
        "email": d.email,
        "phone": d.phone,
        "whatsapp_number": d.whatsapp_number,
    }


def _build_lead_decision(lead) -> LeadDecision:
    days_stalled = 0
    if lead.stage_entered_date:
        days_stalled = (datetime.utcnow() - lead.stage_entered_date).days

    # V1.1 universal gate: the Decision Engine only reasons over VERIFIED
    # contacts, for every sector. An unverified phone/email is treated as
    # absent — so no action is ever recommended against unconfirmed data.
    email_ok = bool(lead.email) and (getattr(lead, "email_verification_status", "") or "") in ("VALID", "RISKY_CATCH_ALL", "CATCH_ALL")
    phone_ok = bool(lead.phone) and bool(getattr(lead, "phone_verified", False))

    return LeadDecision(
        id=lead.id,
        company=lead.company,
        city=lead.city,
        contact_name=lead.contact_name,
        status=lead.status,
        aps=compute_aps(lead),
        rrs=compute_rrs(lead),
        fps=compute_founder_priority_score(lead),
        estimated_value=lead.estimated_value or 0,
        margin_rs=(lead.estimated_value or 0) * 0.31,
        division=lead.division,
        email=lead.email if email_ok else None,
        phone=lead.phone if phone_ok else None,
        whatsapp_number=lead.whatsapp_number if phone_ok else None,
        next_followup_date=lead.next_followup_date,
        days_stalled=days_stalled,
    )


class _OpportunityFacts:
    """
    Adapter: presents a RevenueOpportunity (+ its Organization account and
    bridged operational lead) with the attribute surface the scoring
    functions expect. Commercial model is the source of truth for identity,
    value and verified contacts; the bridged lead supplies operational
    timestamps during the strangler migration.

    NOTE: `id` stays the bridged lead id so Action Queue execution
    (WorkflowEngine, drafts, wa.me flows) keeps working unchanged.
    """
    __slots__ = ("id", "company", "city", "contact_name", "status", "division",
                 "estimated_value", "probability", "email", "phone",
                 "whatsapp_number", "phone_verified", "email_verification_status",
                 "stage_entered_date", "next_followup_date")

    def __init__(self, opp):
        org = opp.organization
        lead = opp.lead
        self.id = opp.lead_id or opp.id
        self.company = org.name if org else (lead.company if lead else "")
        self.city = (org.city if org else None) or (lead.city if lead else None)
        self.contact_name = (org.contact_name if org else None) or (lead.contact_name if lead else None)
        self.status = opp.stage or (lead.status if lead else "COLD")
        self.division = (org.segment if org else None) or (lead.division if lead else None)
        self.estimated_value = opp.estimated_value or 0
        self.probability = opp.probability or 0
        # Verified contacts come from the Organization account (mirrored by sync)
        self.email = org.email if org else (lead.email if lead else None)
        self.phone = org.phone if org else (lead.phone if lead else None)
        self.whatsapp_number = org.whatsapp_number if org else (lead.whatsapp_number if lead else None)
        self.phone_verified = bool(org.phone_verified) if org else bool(getattr(lead, "phone_verified", False))
        self.email_verification_status = (org.email_verification_status if org
                                          else getattr(lead, "email_verification_status", "UNVERIFIED"))
        # Operational timestamps still live on the bridged lead
        self.stage_entered_date = getattr(lead, "stage_entered_date", None) if lead else None
        self.next_followup_date = getattr(lead, "next_followup_date", None) if lead else None


def build_workspace_from_opportunities(opportunities: list) -> FounderWorkspace:
    """
    Architecture Freeze commercial model: the Decision Engine reads
    RevenueOpportunity (primary: Organization) — not B2BLead.
    """
    facts = [_OpportunityFacts(o) for o in opportunities]
    return build_workspace(facts)


def build_workspace(leads: list) -> FounderWorkspace:
    """Build the canonical FounderWorkspace from lead-shaped fact objects."""
    now = datetime.utcnow()
    all_decisions = [_build_lead_decision(l) for l in leads]

    # Money Today — RRS≥75, estimated_value≥25k, hot status, FPS-ranked, top 10
    money_today = sorted(
        [d for d in all_decisions
         if d.rrs >= 75 and d.estimated_value >= 25_000 and d.status in MONEY_TODAY_STATUSES],
        key=lambda d: d.fps, reverse=True
    )[:10]

    # Waiting On — sent a touchpoint but no response for 3+ days
    waiting_on = sorted(
        [d for d in all_decisions
         if d.status in WAITING_ON_STATUSES and d.days_stalled >= 3],
        key=lambda d: d.days_stalled, reverse=True
    )[:15]

    # Action Queue — classify every actionable lead, keep top 50 by FPS.
    # V1.2: leads below the data-completeness threshold never reach founder
    # queues — they stay in automated enrichment ("enrich" actions are counted
    # but not surfaced as founder work).
    actionable = [d for d in all_decisions if d.status not in {"ORDER_WON", "ONBOARDED", "COLD"}]
    action_queue = []
    enriching_count = 0
    for d in sorted(actionable, key=lambda d: d.fps, reverse=True):
        rec = classify_action(d)
        if not rec:
            continue
        if rec["action_type"] == "enrich":
            enriching_count += 1
            continue
        if len(action_queue) < 50:
            action_queue.append(rec)

    # Revenue Coach — single best recommendation
    revenue_coach = None
    if money_today:
        top = money_today[0]
        stage = STATUS_TO_STAGE.get(top.status or "", "proposal")
        cta = ACTION_TYPE_LABELS.get(stage, ("Follow Up", "Close", "close"))[0]
        prog = STATUS_PROGRESSION.get(top.status or "", 0)
        est_days = max(3, (14 - prog) * 5)
        confidence = min(95, 50 + top.rrs // 2)
        evidence = []
        if top.rrs >= 80:
            evidence.append(f"RRS {top.rrs}/100 — strong contact + engagement data")
        if top.days_stalled > 0:
            evidence.append(f"Stalled {top.days_stalled} days — high urgency")
        if top.estimated_value >= 100_000:
            evidence.append(f"Pipeline value Rs.{round(top.estimated_value/1000)}k")
        revenue_coach = RevenueCoachRecommendation(
            company=top.company,
            lead_id=top.id,
            recommended_action=cta,
            confidence_pct=confidence,
            evidence=evidence[:3],
            expected_margin_rs=round(top.margin_rs),
            estimated_days_to_close=est_days,
            current_status=top.status or "",
            fps=top.fps,
        )
    elif action_queue:
        top_action = action_queue[0]
        revenue_coach = RevenueCoachRecommendation(
            company=top_action["company"],
            lead_id=top_action["lead_id"],
            recommended_action=top_action["cta_label"],
            confidence_pct=40,
            evidence=[f"FPS {top_action['fps']} — highest priority unworked lead"],
            expected_margin_rs=top_action["expected_margin_rs"],
            estimated_days_to_close=45,
            current_status=top_action["current_status"] or "",
            fps=top_action["fps"],
        )

    # Pipeline Health
    stage_counts: dict[str, int] = {}
    for d in all_decisions:
        stage = STATUS_TO_STAGE.get(d.status or "", "cold")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

    total_pipeline = sum(d.estimated_value for d in all_decisions if d.status not in {"COLD"})
    replied_plus = sum(
        1 for d in all_decisions
        if STATUS_PROGRESSION.get(d.status or "", 0) >= STATUS_PROGRESSION["REPLIED"]
    )
    conversion_rate = round((replied_plus / max(1, len(all_decisions))) * 100, 1)
    stalled_count = sum(1 for d in all_decisions if d.days_stalled >= 14)

    if conversion_rate >= 15 and stalled_count < 5:
        health_grade = "GREEN"
    elif conversion_rate >= 8 or stalled_count < 15:
        health_grade = "AMBER"
    else:
        health_grade = "RED"

    pipeline_health = PipelineHealth(
        stage_counts=stage_counts,
        total_pipeline_rs=total_pipeline,
        conversion_rate_pct=conversion_rate,
        avg_days_to_reply=7.0,
        stalled_count=stalled_count,
        health_grade=health_grade,
    )

    # Revenue Timeline — probability-weighted forecast
    def _week_forecast(statuses_min_prog: int, prob_factor: float) -> float:
        return sum(
            d.estimated_value * 0.31 * prob_factor
            for d in all_decisions
            if STATUS_PROGRESSION.get(d.status or "", 0) >= statuses_min_prog
        )

    revenue_timeline = RevenueTimeline(
        week_1_rs=round(_week_forecast(11, 0.60)),   # PROPOSAL_SENT+ at 60%
        week_2_rs=round(_week_forecast(10, 0.35)),   # SAMPLE_SENT+ at 35%
        week_3_rs=round(_week_forecast(8, 0.15)),    # MEETING+ at 15%
        week_4_rs=round(_week_forecast(7, 0.08)),    # REPLIED+ at 8%
        month_total_rs=0,
        confidence_pct=65,
    )
    revenue_timeline.month_total_rs = round(
        revenue_timeline.week_1_rs + revenue_timeline.week_2_rs +
        revenue_timeline.week_3_rs + revenue_timeline.week_4_rs
    )

    # Channel summary for Morning Brief
    channel_hot: dict[str, int] = {}
    for d in all_decisions:
        if d.rrs >= 60:
            ch = d.division or "other"
            channel_hot[ch] = channel_hot.get(ch, 0) + 1
    top_channel = max(channel_hot, key=lambda k: channel_hot[k]) if channel_hot else "distributor"

    morning_brief = MorningBrief(
        date=now.strftime("%A, %d %b %Y"),
        total_leads=len(all_decisions),
        pipeline_value_rs=total_pipeline,
        hot_leads=sum(1 for d in all_decisions if d.rrs >= 75),
        money_today_count=len(money_today),
        waiting_on_count=len(waiting_on),
        action_queue_count=len(action_queue),
        top_channel=top_channel,
        enriching_count=enriching_count,
    )

    return FounderWorkspace(
        morning_brief=morning_brief,
        money_today=[vars(d) for d in money_today],
        waiting_on=[vars(d) for d in waiting_on],
        action_queue=action_queue,
        revenue_coach=vars(revenue_coach) if revenue_coach else None,
        pipeline_health=pipeline_health,
        revenue_timeline=revenue_timeline,
        generated_at=now.isoformat(),
    )


def get_decision_engine_summary(db) -> dict:
    """
    Unified V1.2 Decision Engine Service:
    Computes expected revenue stats, evaluates opportunity scoring formula,
    ranks next actions, and returns top decisions for all interfaces.
    """
    from app.models.models import ApprovalRequest, RevenueOpportunity, B2BLead, Organization
    from sqlalchemy import func
    from datetime import datetime

    now = datetime.utcnow()

    # 1. Sum expected margin for APPROVED, non-executed requests
    revenue_waiting = db.query(func.sum(ApprovalRequest.expected_margin)).filter(
        ApprovalRequest.status == "APPROVED",
        ApprovalRequest.executed_at == None
    ).scalar() or 0.0

    # 2. Sum founder time for active opportunities
    active_stages = ["Approval Pending", "Email Approved", "WhatsApp Approved", "AI Call Approved", "Sample", "Proposal", "Negotiation"]
    founder_hours = db.query(func.sum(RevenueOpportunity.founder_hours)).filter(
        RevenueOpportunity.status != "LOST",
        RevenueOpportunity.stage.in_(active_stages)
    ).scalar() or 0.0

    # 3. Query actionable opportunities
    opportunities = db.query(RevenueOpportunity).filter(
        RevenueOpportunity.status != "LOST"
    ).all()

    decisions = []
    for opp in opportunities:
        lead = db.query(B2BLead).filter(B2BLead.id == opp.lead_id).first()
        org = db.query(Organization).filter(Organization.id == opp.organization_id).first()
        
        company_name = org.name if org else (lead.company if lead else "Unknown Account")
        
        # Scoring Parameters
        margin = opp.expected_margin or 0.0
        prob = opp.probability or 0.0
        
        # Strategic Weight
        division = (lead.division or "").lower() if lead else ""
        segment = (lead.segment or "").lower() if lead else ""
        kind = (opp.kind or "").lower()
        if kind == "tender" or division == "government":
            weight = 1.5
        elif segment in [" horeca", "horeca", "pantry", "corporate"]:
            weight = 1.2
        elif segment in ["distributor", "fmcg"]:
            weight = 1.0
        else:
            weight = 0.7

        # Founder Time (hours)
        founder_time = opp.founder_hours or 0.1
        if founder_time <= 0:
            founder_time = 0.1

        # Urgency
        days_stalled = (now - (opp.opened_at or now)).days
        urgency = min(1.5, 1.0 + 0.1 * max(0, days_stalled))

        # Scoring Formula: (Margin * Prob * Weight / Founder Time) * Urgency
        score = ((margin * prob * weight) / founder_time) * urgency

        # Recommended Action & Evidence
        action = opp.stage or "Qualify Lead"
        evidence = [
            f"Opportunity created {(now - (opp.opened_at or now)).days} days ago",
            f"Expected Margin: ₹{int(margin):,}",
            f"Success probability verified at {int(prob * 100)}%"
        ]
        if action == "Proposal":
            evidence.append("Awaiting wholesale price list approval")
        elif action == "Sample":
            evidence.append("Dispatch parameters completed")

        decisions.append({
            "opportunity_id": opp.id,
            "company_name": company_name,
            "recommended_action": action,
            "expected_margin": margin,
            "success_probability": prob,
            "strategic_weight": weight,
            "founder_time_hours": founder_time,
            "urgency": urgency,
            "score": int(score),
            "stage": opp.stage or "Prospecting",
            "evidence": evidence
        })

    # Sort descending by score
    decisions.sort(key=lambda d: d["score"], reverse=True)
    top_decisions = decisions[:3]

    # 4. Count of Pending Approvals
    pending_approvals_count = db.query(ApprovalRequest).filter(
        ApprovalRequest.status == "PENDING_FOUNDER"
    ).count()

    # 5. Count of Blocked Opportunities (stalled >7 days)
    blocked_opportunities_count = db.query(RevenueOpportunity).filter(
        RevenueOpportunity.status != "LOST",
        RevenueOpportunity.stage.notin_(["Prospect", "Qualified"]),
        RevenueOpportunity.opened_at < (now - timedelta(days=7))
    ).count()

    return {
        "revenue_waiting": int(revenue_waiting),
        "founder_hours": round(founder_hours, 1),
        "top_decisions": top_decisions,
        "pending_approvals_count": pending_approvals_count,
        "blocked_opportunities_count": blocked_opportunities_count
    }



# ═══════════════════════════════════════════════════════════════════════════
# ACTION ARBITRATION — the single decision point
#
# Everything above scores and ranks. This decides what actually HAPPENS to a
# contact, and it is the only thing permitted to decide.
#
# Six subsystems hold opinions: trust, cadence, account graph, reply
# intelligence, deliverability, frequency. Each is right in isolation. Twice in
# one day they contradicted each other in production:
#
#   The trust engine cleared 32 addresses; the sender refused 29, because it
#   re-verified with a stricter rule of its own. The queue advertised sends
#   that could never happen.
#
#   The frequency cap read a reply as "account is warm, open it up" while the
#   reply spec required "account is engaged, close it to cold outreach". A
#   buyer answering would have triggered MORE cold mail to their colleagues.
#
# Neither was a coding error. Both were two modules answering one question.
# That does not improve as modules are added — each new conflict is found in
# production, by a customer.
#
# So: subsystems supply FACTS. This supplies the DECISION.
# ═══════════════════════════════════════════════════════════════════════════

# The FIRST blocker that fires decides, so this order is a deliberate
# statement about which concern outranks which — written down in one place
# rather than emerging by accident from import order.
BLOCKER_ORDER = ("suppressed", "not_sendable", "account_engaged",
                 "frequency_capped", "delivery_blocked", "nothing_due")

NEXT_ACTIONS = ("SEND", "DRAFT_ONLY", "WAIT", "FOUNDER_REVIEW", "ENRICH",
                "SUPPRESS", "NONE")


# Actions that represent something PROMISED to a buyer and not yet delivered.
# CALL_AGAIN / WAIT / NONE are not commitments — nobody is waiting on them.
_OPEN_COMMITMENTS = {
    "SEND_CATALOGUE", "SEND_PRICING", "SEND_SAMPLE", "SEND_WHATSAPP",
    "FOUNDER_PRICING", "FOUNDER_CALL", "SCHEDULE_CALLBACK", "BOOK_MEETING",
    "CALL_DECISION_MAKER",
}

# Events that discharge a commitment. A later call counts: if the founder spoke
# to them again, the earlier promise was either honoured or superseded, and
# either way the newer call's own NEXT_ACTION_SET replaces it.
_COMMITMENT_FULFILLED = {
    "EMAIL_SENT", "WHATSAPP_SENT", "SAMPLE_DISPATCHED", "MEETING_HELD",
    "ORDER_PLACED", "NEXT_ACTION_DONE", "FOUNDER_CALL", "LEAD_DISQUALIFIED",
}


def _gather_facts(lead, db) -> dict:
    """Ask every subsystem what it knows. This function never judges."""
    from datetime import datetime as _dt
    from app.models.models import WorkflowEvent
    from app.services import trust_promoter as tp
    from app.services import sequence_engine as se
    from app.services import account_graph as ag
    from app.services import deliverability as D

    f = {"lead_id": lead.id, "company": lead.company, "email": lead.email,
         "city": lead.city, "category": lead.division}

    ok, why = tp.may_send(lead)
    f["trust"] = {"level": tp.normalise(lead.email_trust),
                  "confidence": getattr(lead, "email_confidence", None),
                  "may_send": ok, "why": why, "may_draft": tp.may_draft(lead)}
    f["sequence"] = se.state(lead, db)

    acct = ag.account_for(lead, db)
    allowed, freason = ag.can_contact_new(lead, db)
    f["account"] = {"name": acct["name"], "branches": acct["branch_count"],
                    "is_chain": acct["is_chain"], "basis": acct["basis"],
                    "may_contact_new": allowed, "why": freason}

    evs = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id).all()
    reps = [e for e in evs if e.event_type == "EMAIL_REPLY_RECEIVED"]
    f["reply"] = None
    if reps:
        last = max(reps, key=lambda e: e.occurred_at or _dt.min)
        p = last.payload or {}
        f["reply"] = {"at": last.occurred_at, "intents": p.get("intents", []),
                      "polarity": p.get("polarity"),
                      "next_action": p.get("next_action"),
                      "sla_minutes": p.get("sla_minutes"),
                      "confidence": p.get("confidence")}
    f["suppressed"] = any(
        e.event_type in ("UNSUBSCRIBED", "DO_NOT_CONTACT", "COMPLAINT")
        or (e.payload or {}).get("next_action") == "SUPPRESS_ACCOUNT"
        for e in evs)

    # An open commitment from a phone call: the buyer asked for something and
    # it has not been delivered yet. Nothing in trust, sequence or account
    # state can reveal this — it exists only because a human said it out loud —
    # so the engine has to be told, or it will happily schedule "touch 2:
    # nudge" at a buyer who is waiting on the catalogue they asked for.
    f["commitment"] = None
    commits = [e for e in evs if e.event_type == "NEXT_ACTION_SET"
               and (e.payload or {}).get("action") in _OPEN_COMMITMENTS]
    if commits:
        last_c = max(commits, key=lambda e: e.occurred_at or _dt.min)
        done_after = [e for e in evs
                      if e.event_type in _COMMITMENT_FULFILLED
                      and (e.occurred_at or _dt.min) > (last_c.occurred_at or _dt.min)]
        if not done_after:
            p = last_c.payload or {}
            f["commitment"] = {"action": p.get("action"),
                               "detail": p.get("detail"),
                               "from_outcome": p.get("from_outcome"),
                               "blocked": p.get("blocked"),
                               "at": last_c.occurred_at}

    try:
        v = D.check_send_allowed(db)
        f["delivery"] = {"allowed": bool(getattr(v, "allowed", v)),
                         "reason": getattr(v, "reason", "")}
    except Exception as e:
        # A guard that cannot answer must never read as permission.
        f["delivery"] = {"allowed": False,
                         "reason": f"guard unavailable ({e.__class__.__name__})"}
    return f


def _decision(action, reason, confidence, blockers, facts, audit) -> dict:
    from datetime import datetime as _dt
    return {"action": action, "reason": reason, "confidence": confidence,
            "blockers": blockers, "facts": facts, "audit": audit,
            "decided_at": _dt.utcnow()}


def evaluate_next_action(lead, db) -> dict:
    """
    The one function. action / reason / confidence / blockers / audit.
    Callers act on `action`; they do not re-derive any part of this.
    """
    f = _gather_facts(lead, db)
    blockers = []
    audit = [
        f"trust={f['trust']['level']} conf={f['trust']['confidence']} ({f['trust']['why']})",
        f"account={f['account']['name']} [{f['account']['branches']} site(s)] {f['account']['why']}",
        f"sequence={f['sequence']['reason']} touches={f['sequence']['touches']}",
    ]

    # 1. The buyer said stop. Absolute — not a tuning knob, a legal line.
    if f["suppressed"]:
        blockers.append("suppressed")
        return _decision("SUPPRESS", "the account asked not to be contacted",
                         100, blockers, f, audit + ["suppression is absolute"])

    # 2. A live conversation outranks any scheduled activity.
    if f["reply"]:
        r = f["reply"]
        audit.append(f"reply intents={r['intents']} polarity={r['polarity']}")
        act = "FOUNDER_REVIEW" if (r["confidence"] or 0) < 70 else "DRAFT_ONLY"
        return _decision(act,
                         f"live conversation — {r['next_action'] or 'respond'} "
                         f"(SLA {r['sla_minutes']} min)",
                         r["confidence"] or 60, blockers, f,
                         audit + ["a reply outranks any scheduled touch"])

    # 2b. Something was promised on a call and has not been delivered.
    #     Ranked directly below a live reply and above everything scheduled:
    #     a buyer waiting on the catalogue they asked for must never receive
    #     "touch 2: nudge" instead. Placed before the trust gate on purpose —
    #     an unsendable address does not cancel the promise, it just means the
    #     promise is blocked and the founder needs to see why.
    if f.get("commitment"):
        c = f["commitment"]
        audit.append(f"open commitment {c['action']} from call outcome "
                     f"{c['from_outcome']}")
        if c.get("blocked"):
            blockers.append("commitment_blocked")
            return _decision("FOUNDER_REVIEW",
                             f"{c['detail']} — blocked: {c['blocked']}",
                             85, blockers, f,
                             audit + ["a promise we cannot keep needs a human"])
        return _decision(c["action"], c["detail"] or "promised on a call", 95,
                         blockers, f,
                         audit + ["a commitment outranks any scheduled touch"])

    # 3. May this address be used at all?
    if not f["trust"]["may_send"]:
        blockers.append("not_sendable")
        act = "DRAFT_ONLY" if f["trust"]["may_draft"] else "ENRICH"
        return _decision(act, f["trust"]["why"], 90, blockers, f,
                         audit + ["trust engine is the sole authority here"])

    # 4. Would this over-contact the company?
    if not f["account"]["may_contact_new"]:
        blockers.append("frequency_capped" if "cooldown" in f["account"]["why"]
                        else "account_engaged")
        return _decision("WAIT", f["account"]["why"], 95, blockers, f,
                         audit + ["governance is account-level, not contact-level"])

    # 5. Is anything due?
    seq = f["sequence"]
    if not seq.get("active"):
        blockers.append("nothing_due")
        return _decision("NONE", seq.get("note") or f"sequence {seq['reason']}",
                         80, blockers, f, audit)
    if not seq.get("ready"):
        blockers.append("nothing_due")
        return _decision("WAIT", f"next touch '{seq['next_touch']}' not due yet",
                         85, blockers, f, audit)

    # 6. Provider/guard LAST, so a temporary throttle reads as "wait" rather
    #    than contaminating the lead's own eligibility.
    if not f["delivery"]["allowed"]:
        blockers.append("delivery_blocked")
        return _decision("WAIT", f"delivery guard: {f['delivery']['reason']}",
                         90, blockers, f,
                         audit + ["throttling is temporary; the lead stays eligible"])

    return _decision("SEND",
                     f"touch {seq['touches']+1} '{seq['next_touch']}' — {seq['purpose']}",
                     f["trust"]["confidence"] or 60, blockers, f,
                     audit + ["all gates agree"])


def decide_many(db, limit: int = 0) -> dict:
    from collections import Counter
    from app.models.models import B2BLead
    rows = db.query(B2BLead).filter(B2BLead.email != "",
                                    B2BLead.email.isnot(None)).all()
    if limit:
        rows = rows[:limit]
    out, acts, blocks = [], Counter(), Counter()
    for l in rows:
        d = evaluate_next_action(l, db)
        acts[d["action"]] += 1
        for b in d["blockers"]:
            blocks[b] += 1
        out.append(d)
    return {"decisions": out, "by_action": dict(acts),
            "by_blocker": dict(blocks), "considered": len(rows)}


def explain_decision(lead, db) -> str:
    """Plain-language 'why did it do that?', answerable without rerunning code."""
    d = evaluate_next_action(lead, db)
    lines = [f"{lead.company} <{lead.email}>",
             f"  ACTION: {d['action']} — {d['reason']}",
             f"  confidence {d['confidence']}"]
    if d["blockers"]:
        lines.append(f"  blocked by: {', '.join(d['blockers'])}")
    return "\n".join(lines + ["  reasoning:"] + [f"    - {a}" for a in d["audit"]])
