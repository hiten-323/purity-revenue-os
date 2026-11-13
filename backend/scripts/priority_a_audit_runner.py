"""
Priority A Lead Audit Runner for Purity Revenue OS V2.0
Authoritative audit of all leads with intent_score >= 80.
"""
import json
from app.database.database import SessionLocal
from app.models.models import B2BLead, LeadEvidence, LeadInteraction, EmailDraft, OutboundWhatsApp, WorkflowEvent
from app.services.customer_segmentation import CustomerSegmentationEngine
from app.services.product_fit_engine import ProductFitEngine
from app.services.outbound_cohort import REVIEWED_ALLOW_LIST

db = SessionLocal()
leads = db.query(B2BLead).filter(B2BLead.intent_score >= 80).order_by(B2BLead.intent_score.desc()).all()
print(f"Total Priority A Leads (score >= 80): {len(leads)}")

results = []
for l in leads:
    evs = db.query(LeadEvidence).filter(LeadEvidence.lead_id == l.id).all()
    ev_texts = [f"{e.signal_type}: {e.value} (src: {e.source_url or e.source})" for e in evs]
    
    seg = CustomerSegmentationEngine.classify(l, evs)
    
    # Check Product fit
    rec = ProductFitEngine.recommend(
        taste_preference=l.qualification_notes or l.coffee_buying_evidence or l.searched_category,
        business_type=l.industry or l.searched_category,
        current_competitor=l.current_brand or l.current_supplier,
        is_first_order=True
    )
    
    # Check Eligibility
    phone_raw = l.phone or ""
    email_raw = l.email or ""
    
    # Voice eligibility
    voice_ok = False
    voice_reason = "No phone"
    if phone_raw:
        norm_phone = "".join(c for c in phone_raw if c.isdigit())[-10:]
        if norm_phone == "8591977190":
            voice_reason = "HARDBLOCKED (8591977190)"
        elif float(l.contact_confidence or 0.0) < 0.75:
            voice_reason = f"Low contact confidence ({l.contact_confidence}) < 0.75"
        else:
            in_allow = l.id in REVIEWED_ALLOW_LIST
            voice_ok = in_allow
            voice_reason = "ELIGIBLE" if in_allow else "Outside allow-list"
            
    # Email eligibility
    email_ok = False
    email_reason = "No email"
    if email_raw:
        if l.email_verification_status != "VALID":
            email_reason = f"Verification status: {l.email_verification_status or 'UNVERIFIED'}"
        else:
            in_allow = l.id in REVIEWED_ALLOW_LIST
            email_ok = in_allow
            email_reason = "ELIGIBLE" if in_allow else "Outside allow-list"
            
    # WhatsApp eligibility
    wa_ok = False
    wa_reason = "No affirmative explicit consent"
    if l.consent_status == "EXPLICIT":
        in_allow = l.id in REVIEWED_ALLOW_LIST
        wa_ok = in_allow
        wa_reason = f"EXPLICIT consent ({l.consent_source or 'unknown'})" + ("" if in_allow else " - Outside allow-list")
    else:
        wa_reason = f"Consent status is {l.consent_status or 'UNKNOWN'}"
        
    # Previous history
    ixs = db.query(LeadInteraction).filter(LeadInteraction.lead_id == l.id).all()
    history = [f"{ix.method}:{ix.outcome}" for ix in ixs]
    
    wfs = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == l.id).all()
    wf_history = [f"{w.event_type}" for w in wfs]
    
    # Buying requirement
    buying_req = ""
    if l.expected_monthly_consumption_kg:
        buying_req += f"Monthly volume: {l.expected_monthly_consumption_kg} kg. "
    if l.current_brand:
        buying_req += f"Current brand: {l.current_brand}. "
    if l.proposal_text:
        buying_req += f"Proposal notes: {l.proposal_text}. "
    if not buying_req:
        buying_req = "Requirement pending detailed discovery call"
        
    # Determine Next Action based on eligibility & segment
    next_action = ""
    if wa_ok:
        next_action = "WhatsApp follow-up with Variety Box COD link (Affirmative consent verified)"
    elif voice_ok:
        next_action = "Priority AI Voice call qualification (Mon-Sat 09:30-19:00 IST)"
    elif email_ok:
        next_action = "Personalized commercial quotation & starter pack invitation"
    else:
        next_action = "Manual founder review required before adding to allow-list"
        
    results.append({
        "id": l.id,
        "company": l.company,
        "contact_name": l.contact_name or "Manager / Owner",
        "lead_source": l.lead_source,
        "intent_score": l.intent_score,
        "coffee_buying_evidence": l.coffee_buying_evidence or "Evidence captured during crawler sweep",
        "evidence_signals": ev_texts,
        "last_updated": str(l.last_updated or l.stage_entered_date),
        "segment": seg.name,
        "product_recommendation": f"{rec.primary_product} | Starter: {rec.starter_pack_name}",
        "buying_requirement": buying_req,
        "voice_eligibility": f"{voice_ok} ({voice_reason})",
        "email_eligibility": f"{email_ok} ({email_reason})",
        "whatsapp_eligibility": f"{wa_ok} ({wa_reason})",
        "next_action": next_action,
        "history": history + wf_history,
        "phone": phone_raw[-10:] if phone_raw else "",
        "email": email_raw
    })

with open("priority_a_audit.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"Successfully exported {len(results)} Priority A leads to priority_a_audit.json")

# Print summary of findings
print("--- ELIGIBILITY SUMMARY ACROSS 46 LEADS ---")
v_count = sum(1 for r in results if r["voice_eligibility"].startswith("True"))
e_count = sum(1 for r in results if r["email_eligibility"].startswith("True"))
w_count = sum(1 for r in results if r["whatsapp_eligibility"].startswith("True"))
print(f"Voice Eligible (Confidence >= 0.75 + in Allow-list): {v_count}")
print(f"Email Eligible (Verified VALID + in Allow-list): {e_count}")
print(f"WhatsApp Eligible (EXPLICIT Consent + in Allow-list): {w_count}")

allow_list_leads = [r for r in results if r["id"] in REVIEWED_ALLOW_LIST]
print(f"Priority A Leads in Allow-List: {[r['id'] for r in allow_list_leads]}")

for r in allow_list_leads:
    print(f"  Lead #{r['id']} ({r['company']}) | Score: {r['intent_score']} | Voice: {r['voice_eligibility']} | Email: {r['email_eligibility']} | WA: {r['whatsapp_eligibility']} | Next: {r['next_action']}")

db.close()
