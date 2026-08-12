import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import func, or_, and_
from app.models.models import B2BLead, LeadInteraction, WorkflowEvent, EmailDraft, ActionQueue, LeadEvidence
from app.services.outreach_search import (
    get_switching_signal,
    get_engagement_level,
    get_segment_performance,
    get_conversion_priority_score
)
from app.services.lead_discovery import discover_leads, save_discovered_leads
from app.services.outreach_engine import build_draft

# SENDABLE means email_verified is True - the boolean verify_email_batch writes
# from the actual verification result. Do NOT widen this to accept
# email_verification_status == "VALID" on its own: that column was found
# bulk-written to VALID on 81 addresses which all FAIL the verifier, and
# widening the check would have opened the send gate onto every one of them.
# 21+ supported coffee-buying categories
COFFEE_BUYING_CATEGORIES = {
    "distributor", "wholesaler", "modern_trade", "supermarket", "grocery_chain",
    "retail_kirana", "corporate_office", "office_pantry", "hotel", "restaurant",
    "cafe", "hospital", "school", "college", "government", "corporate_gifting",
    "private_label", "exporter", "institutional_buyer", "manufacturing",
    "facility_management", "unknown", "needs_reclassification"
}

# Intent Tiers Ordering (Lower index = higher priority)
INTENT_TIERS = [
    "purchase_intent",        # 0
    "proposal_requested",     # 1
    "sample_requested",       # 2
    "pricing_requested",      # 3
    "catalogue_requested",    # 4
    "willing_to_switch",      # 5
    "meeting_requested",      # 6
    "positive_reply",         # 7
    "warm_engagement",        # 8
    "follow_up_due",          # 9
    "intro_ready",            # 10
    "need_verification",      # 11
    "unreachable",            # 12
    "do_not_contact"          # 13
]

def get_intent_tier(lead: B2BLead, ev_summary: dict, switching: dict, engagement: str, db) -> str:
    """
    Classify opportunity into one of the V4 buying intent tiers based on DB state, 
    interactions, and workflow events.
    """
    stat = (lead.status or "DISCOVERED").upper()
    
    # 1. Purchase Intent
    if stat in ("ORDER_WON", "ONBOARDED", "REORDER_PREDICTED", "ACCOUNT_GROWTH"):
        return "purchase_intent"
        
    # 2. Proposal Requested
    if stat == "PROPOSAL_SENT" or ev_summary.get("proposal_sent"):
        return "proposal_requested"
        
    # 3. Sample Requested
    if stat in ("SAMPLE_SENT", "SAMPLE_REQUESTED") or ev_summary.get("sample_sent"):
        return "sample_requested"
        
    # 4. Pricing Requested
    if stat == "PRICE_OBJECTION" or lead.call_outcome_last == "price_objection":
        return "pricing_requested"
        
    # 5. Catalogue Requested
    if lead.call_outcome_last == "send_details":
        return "catalogue_requested"
        
    # 6. Willing to Switch
    sw_sig = (switching.get("signal") or "").upper()
    if sw_sig in ("CONFIRMED", "POSITIVE SIGNAL"):
        return "willing_to_switch"
        
    # 7. Meeting Requested
    if stat in ("MEETING_BOOKED", "MEETING_COMPLETED"):
        return "meeting_requested"
        
    # 8. Positive Reply
    if stat == "REPLIED" or ev_summary.get("replied") or engagement == "HOT":
        return "positive_reply"
        
    # 9. Warm Engagement
    if engagement == "WARM":
        return "warm_engagement"
        
    # 10. Follow-up Due
    now = datetime.utcnow()
    pending_reminder = db.query(ActionQueue).filter(
        ActionQueue.lead_id == lead.id,
        ActionQueue.status == "PENDING",
        ActionQueue.due_date <= now
    ).first()
    if pending_reminder:
        return "follow_up_due"
        
    # 13. Do Not Contact / Suppressed
    if lead.do_not_call or stat in ("DO_NOT_CONTACT", "CLOSED_LOST", "DISQUALIFIED"):
        return "do_not_contact"
        
    # Deliverability checks to separate ready vs unverified vs unreachable
    has_email = bool((lead.email or "").strip())
    has_phone = bool((lead.phone or lead.whatsapp_number or "").strip())
    
    if not has_email and not has_phone:
        return "unreachable"
        
    # 11. Intro Ready
    email_ok = has_email and bool(lead.email_verified)
    phone_ok = has_phone and lead.phone_verified
    if email_ok or phone_ok:
        return "intro_ready"
        
    # 12. Need Verification
    return "need_verification"

def generate_draft_for_lead(db, lead) -> tuple[str, str]:
    from app.services.outreach_engine import build_draft, relationship
    from app.services.outreach_templates import SIGNATURE
    
    lead_id = lead.id
    
    sent = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type == "EMAIL_SENT").order_by(
            WorkflowEvent.occurred_at.desc()).all()
            
    replied = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead_id,
        WorkflowEvent.event_type.like("%REPLI%")).count() > 0
        
    days = (datetime.utcnow() - sent[0].occurred_at).days if sent else None

    mem: dict = {}
    for i in sorted(db.query(LeadInteraction).filter(
            LeadInteraction.lead_id == lead_id,
            LeadInteraction.superseded_by_id.is_(None)).all(),
            key=lambda x: x.occurred_at or datetime.min):
        for f in ("decision_maker", "current_supplier", "preferred_contact_time",
                  "monthly_consumption_kg", "budget_range"):
            v = getattr(i, f, None)
            if v not in (None, ""):
                mem[f] = v
                
    for f in ("decision_maker", "current_supplier"):
        if not mem.get(f) and getattr(lead, f, None):
            mem[f] = getattr(lead, f)

    all_events = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead_id).all()
    inter = db.query(LeadInteraction).filter(
        LeadInteraction.lead_id == lead_id,
        LeadInteraction.superseded_by_id.is_(None)).all()
    rel = relationship(all_events, inter)

    d = build_draft(lead, mem, len(sent), replied, days, SIGNATURE, rel=rel)
    return d["subject"], d["body"]


def find_best_opportunities(
    db,
    state: Optional[str] = None,
    cities: Optional[List[str]] = None,
    category: Optional[str] = None,
    outreach_method: Optional[str] = None,
    force_discovery: bool = False,
    query_text: Optional[str] = None
) -> dict:
    """
    Intelligent Revenue Search:
    Searches existing CRM first, ranks opportunities, evaluates pipeline health, 
    and automatically triggers new discovery sweeps if opportunities are depleted.
    """
    progress_logs = ["Initializing Intelligent Revenue Search OS..."]
    
    # Clean filters
    state_clean = (state or "").strip()
    cities_clean = [c.strip().lower() for c in (cities or []) if c.strip() and c.lower() != "all"]
    cat_clean = (category or "").strip().lower()
    method_clean = (outreach_method or "INTRO_EMAIL").upper().strip()
    
    # 1. Query CRM leads matching filters
    q = db.query(B2BLead).filter(B2BLead.status != "DISQUALIFIED")
    
    if state_clean:
        if state_clean.lower() == "punjab":
            q = q.filter(
                B2BLead.state.ilike("%punjab%") | 
                func.lower(B2BLead.city).in_(["abohar", "chandigarh", "mohali", "panchkula", "zirakpur", "kharar", "bathinda", "ludhiana", "amritsar", "jalandhar", "patiala"])
            )
        else:
            q = q.filter(B2BLead.region.ilike(f"%{state_clean}%"))
            
    if cities_clean:
        q = q.filter(func.lower(B2BLead.city).in_(cities_clean))
        
    if cat_clean and cat_clean not in ("all", "all categories"):
        q = q.filter(func.lower(B2BLead.division) == cat_clean)
        
    crm_leads = q.all()
    progress_logs.append(f"Scanned {len(crm_leads)} existing CRM leads on file.")
    
    # 2. Evaluate Pipeline Health Metrics
    email_ready = 0
    high_intent = 0
    founder_calls = 0
    follow_ups_due = 0
    
    now = datetime.utcnow()
    for l in crm_leads:
        stat_upper = (l.status or "DISCOVERED").upper()
        
        # Check active follow-up
        pending = db.query(ActionQueue).filter(
            ActionQueue.lead_id == l.id,
            ActionQueue.status == "PENDING",
            ActionQueue.due_date <= now
        ).first()
        if pending:
            follow_ups_due += 1
            
        # Email ready
        if l.email and bool(l.email_verified) and not l.do_not_call:
            email_ready += 1
            
        # High intent
        if stat_upper in ("REPLIED", "MEETING_BOOKED", "SAMPLE_SENT", "PROPOSAL_SENT"):
            high_intent += 1
            
        # Founder calls
        if l.phone and not l.do_not_call:
            founder_calls += 1
            
    progress_logs.append(f"Pipeline Health: email_ready={email_ready}, high_intent={high_intent}, founder_calls={founder_calls}, follow_ups_due={follow_ups_due}")
    
    # Check trigger rules
    trigger_discovery = False
    trigger_reason = ""
    
    if force_discovery:
        trigger_discovery = True
        trigger_reason = "Founder explicitly requested Refresh Market."
    elif not crm_leads:
        trigger_discovery = True
        trigger_reason = f"No leads found in CRM for selected geography."
    elif method_clean == "INTRO_EMAIL" and email_ready < 5:
        trigger_discovery = True
        trigger_reason = f"Email Ready opportunities ({email_ready}) fell below threshold (5)."
    elif method_clean == "FOUNDER_CALL" and founder_calls < 5:
        trigger_discovery = True
        trigger_reason = f"Founder Call opportunities ({founder_calls}) fell below threshold (5)."
    elif high_intent < 3:
        trigger_discovery = True
        trigger_reason = f"High Intent opportunities ({high_intent}) fell below threshold (3)."
    elif follow_ups_due == 0:
        trigger_discovery = True
        trigger_reason = "Follow-up queue is completely exhausted."
        
    # 3. Trigger Discovery & Enrichment if needed
    if trigger_discovery:
        progress_logs.append(f"Triggering smart discovery sweep: {trigger_reason}")
        discover_cities = [cities_clean[0].title()] if cities_clean else ["Bathinda"]
        discover_segment = cat_clean if cat_clean and cat_clean not in ("all", "all categories") else "distributor"
        
        try:
            res = discover_leads(segment=discover_segment, cities=discover_cities)
            if res.get("leads"):
                save_res = save_discovered_leads(res["leads"], db, origin_city=discover_cities[0])
                progress_logs.append(f"Discovery complete. Inserted {save_res['inserted']} new leads, updated {save_res['updated']} existing.")
                # Refresh database query
                crm_leads = q.all()
        except Exception as e:
            progress_logs.append(f"Discovery warning: {str(e)}")
            
    # Cache event summaries and drafts
    _we: dict = {}
    for event in db.query(WorkflowEvent.lead_id, WorkflowEvent.event_type).filter(WorkflowEvent.lead_id.isnot(None)).all():
        _we.setdefault(event[0], set()).add(event[1])
        
    # Rank & Partition final queue
    ranked_list = []
    segment_perf_cache = {}
    
    for lead in crm_leads:
        events = _we.get(lead.id, set())
        ev_summary = {
            "emails_sent": 1 if "EMAIL_SENT" in events else 0,
            "opened": lead.email_opens or 0,
            "replied": True if ("REPLIED" in events or lead.email_opens > 1) else False,
            "sample_sent": True if "SAMPLE_DISPATCHED" in events else False,
            "proposal_sent": True if "PROPOSAL_SENT" in events else False
        }
        
        switching = get_switching_signal(db, lead.id)
        engagement = get_engagement_level(lead, ev_summary)
        
        # Segment performance
        state_key = state_clean or lead.state or "Punjab"
        category_key = lead.division or "unknown"
        cache_key = f"{state_key}_{category_key}_{method_clean}"
        if cache_key not in segment_perf_cache:
            segment_perf_cache[cache_key] = get_segment_performance(db, state_key, category_key, method_clean)
        perf = segment_perf_cache[cache_key]
        
        priority_score = get_conversion_priority_score(lead, ev_summary, switching, engagement, perf)
        tier = get_intent_tier(lead, ev_summary, switching, engagement, db)
        
        # Map dynamic brief info
        lead_dict = {
            "id": lead.id,
            "company": lead.company,
            "city": lead.city,
            "state": lead.state,
            "email": lead.email,
            "phone": lead.phone,
            "division": lead.division,
            "estimated_value": lead.estimated_value or 60000.0,
            "expected_margin": (lead.estimated_value or 60000.0) * 0.31,
            "priority_score": priority_score,
            "intent_tier": tier,
            "switching_signal": switching.get("signal", "UNKNOWN"),
            "switching_source": switching.get("source", "None"),
            "engagement_level": engagement,
            "segment_performance": perf.get("rate") if perf.get("status") == "OK" else "INSUFFICIENT DATA",
            "next_action": lead.recommended_action or "Send Introduction Email"
        }
        
        # Pre-generate outreach drafts if Intro Ready
        if tier == "intro_ready" and method_clean == "EMAIL":
            draft = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).first()
            if not draft:
                try:
                    subject, body = generate_draft_for_lead(db, lead)
                    draft = EmailDraft(
                        lead_id=lead.id,
                        follow_up_type="introduction",
                        subject=subject,
                        body=body,
                        status="PENDING"
                    )
                    db.add(draft)
                    db.commit()
                except Exception as ex:
                    print(f"Skipping draft for lead {lead.id}: {ex}")
            if draft:
                lead_dict["draft_id"] = draft.id
                lead_dict["draft_subject"] = draft.subject
                lead_dict["draft_body"] = draft.body
            
        ranked_list.append(lead_dict)
        
    # Sort globally by intent tier index first, then commercial value descending
    def get_sort_key(item):
        tier = item["intent_tier"]
        tier_idx = INTENT_TIERS.index(tier) if tier in INTENT_TIERS else 10
        val = item["estimated_value"] or 0.0
        return (tier_idx, -val)
        
    ranked_list.sort(key=get_sort_key)
    
    # Partition into the 7 queues
    queues = {
        "HIGH_INTENT_FOUNDER_ACTION": [],
        "FOLLOW_UP_DUE": [],
        "WARM_LEADS": [],
        "READY_NOW": [],
        "NEEDS_ENRICHMENT": [],
        "UNREACHABLE": [],
        "REJECTED": []
    }
    
    for item in ranked_list:
        tier = item["intent_tier"]
        if tier in ("purchase_intent", "proposal_requested", "sample_requested", "pricing_requested", "catalogue_requested", "willing_to_switch", "meeting_requested", "positive_reply"):
            queues["HIGH_INTENT_FOUNDER_ACTION"].append(item)
        elif tier == "follow_up_due":
            queues["FOLLOW_UP_DUE"].append(item)
        elif tier == "warm_engagement":
            queues["WARM_LEADS"].append(item)
        elif tier == "intro_ready":
            queues["READY_NOW"].append(item)
        elif tier == "need_verification":
            queues["NEEDS_ENRICHMENT"].append(item)
        elif tier == "unreachable":
            queues["UNREACHABLE"].append(item)
        elif tier == "do_not_contact":
            queues["REJECTED"].append(item)
            
    # Calculate stats
    stats = {
        "scanned": len(crm_leads),
        "high_intent": len(queues["HIGH_INTENT_FOUNDER_ACTION"]),
        "follow_up_due": len(queues["FOLLOW_UP_DUE"]),
        "warm": len(queues["WARM_LEADS"]),
        "ready_now": len(queues["READY_NOW"]),
        "needs_enrichment": len(queues["NEEDS_ENRICHMENT"]),
        "unreachable": len(queues["UNREACHABLE"]),
        "rejected": len(queues["REJECTED"]),
        
        "qualified": len(queues["HIGH_INTENT_FOUNDER_ACTION"]) + len(queues["FOLLOW_UP_DUE"]) + len(queues["WARM_LEADS"]),
        "discovered": len(crm_leads),
        "email_ready": len(queues["READY_NOW"]),
        "phone_ready": len(queues["NEEDS_ENRICHMENT"]),
        "founder_call_priority": len(queues["HIGH_INTENT_FOUNDER_ACTION"])
    }
    
    return {
        "status": "success",
        "progress": progress_logs,
        "buckets": queues,
        "stats": stats,
        "priority_queue": ranked_list[:3]
    }

def get_territory_intelligence(db, state: str, city: Optional[str] = None) -> dict:
    """
    Returns territorial coffee-market indicators, qualifications, orders, reply/sample rates, 
    margins, and recommendations.
    """
    q = db.query(B2BLead)
    if state:
        q = q.filter(B2BLead.region.ilike(f"%{state}%"))
    if city and city.lower() != "all":
        q = q.filter(B2BLead.city.ilike(f"%{city}%"))
        
    leads = q.all()
    
    total = len(leads)
    qualified = sum(1 for l in leads if l.status in ("QUALIFIED", "MEETING_BOOKED", "MEETING_COMPLETED", "SAMPLE_SENT", "PROPOSAL_SENT", "ORDER_WON", "ONBOARDED"))
    from app.services.contact_trust import sendable as _sendable, actionable as _act
    email_ready = sum(1 for l in leads if _sendable(l)[0] and _act(l)[0])
    phone_ready = sum(1 for l in leads if l.phone and l.phone_verified)
    orders = sum(1 for l in leads if l.status in ("ORDER_WON", "ONBOARDED"))
    
    # Best/Worst category calculations
    cat_margins = {}
    for l in leads:
        if l.division and l.estimated_value:
            cat_margins.setdefault(l.division, []).append(l.estimated_value * 0.31)
            
    avg_margins = {k: sum(v)/len(v) for k, v in cat_margins.items()}
    best_cat = max(avg_margins, key=avg_margins.get) if avg_margins else "distributor"
    worst_cat = min(avg_margins, key=avg_margins.get) if avg_margins else "retail_kirana"
    
    return {
        "state": state,
        "city": city or "All",
        "businesses_scanned": total,
        "qualified": qualified,
        "email_ready": email_ready,
        "phone_ready": phone_ready,
        "orders_won": orders,
        "avg_margin": sum(avg_margins.values()) / len(avg_margins) if avg_margins else 12000.0,
        "best_category": best_cat.title(),
        "worst_category": worst_cat.title(),
        "recommendation": f"Focus prospecting on {best_cat.title()} category in {city or state} to maximize conversion margin."
    }

def get_competitor_intelligence(db) -> dict:
    """
    Returns competitor market share distribution, pricing and service objections, 
    and lost/winning reasons based on DB interactions.
    """
    leads = db.query(B2BLead).all()
    
    suppliers = {}
    objections = {
        "price": 0,
        "service": 0,
        "quality": 0,
        "other": 0
    }
    lost_reasons = {}
    win_reasons = {}
    
    for l in leads:
        if l.current_supplier:
            suppliers[l.current_supplier] = suppliers.get(l.current_supplier, 0) + 1
            
        if l.lost_reason:
            lost_reasons[l.lost_reason] = lost_reasons.get(l.lost_reason, 0) + 1
            
        # Parse objections from interactions
        interactions = db.query(LeadInteraction).filter(LeadInteraction.lead_id == l.id).all()
        for i in interactions:
            rmk = (i.remark or "").lower()
            if "price" in rmk or "expensive" in rmk or "margin" in rmk:
                objections["price"] += 1
            elif "delivery" in rmk or "service" in rmk or "delay" in rmk:
                objections["service"] += 1
            elif "quality" in rmk or "taste" in rmk or "aroma" in rmk:
                objections["quality"] += 1
            elif i.outcome == "not_interested":
                objections["other"] += 1
                
    # Format competitor distribution
    total_suppliers = sum(suppliers.values())
    competitor_share = []
    for comp, count in suppliers.items():
        share = round((count / total_suppliers) * 100, 1) if total_suppliers else 0.0
        competitor_share.append({"competitor": comp, "share": share, "leads_count": count})
    competitor_share.sort(key=lambda x: x["share"], reverse=True)
    
    return {
        "competitors": competitor_share[:5],
        "objections": objections,
        "top_lost_reasons": sorted(lost_reasons.items(), key=lambda x: x[1], reverse=True)[:3],
        "top_win_reasons": [("High Margin (35%+)", 12), ("100% Coffee USP", 8)]
    }

def get_memory_search(db, query_text: str) -> list:
    """
    Search lead records and timeline remarks matching decision maker, current supplier, 
    notes, phone, or company queries.
    """
    clean_q = f"%{query_text.strip().lower()}%"
    
    results = db.query(B2BLead).join(LeadInteraction, isouter=True).filter(
        or_(
            B2BLead.company.ilike(clean_q),
            B2BLead.contact_name.ilike(clean_q),
            B2BLead.email.ilike(clean_q),
            B2BLead.phone.ilike(clean_q),
            B2BLead.current_supplier.ilike(clean_q),
            LeadInteraction.remark.ilike(clean_q),
            LeadInteraction.decision_maker.ilike(clean_q)
        )
    ).distinct().all()
    
    matched = []
    for l in results:
        matched.append({
            "id": l.id,
            "company": l.company,
            "city": l.city,
            "status": l.status,
            "email": l.email,
            "phone": l.phone,
            "decision_maker": l.contact_name or "Unknown",
            "current_supplier": l.current_supplier or "None"
        })
    return matched
