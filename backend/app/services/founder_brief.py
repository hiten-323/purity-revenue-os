"""
Founder Daily Brief — generates a morning summary for Hiten.
Sends via Zoho SMTP to hitenjain.12@gmail.com.
Also returns the brief as a dict so the dashboard can display it.
"""
from __future__ import annotations
import os
from datetime import datetime, date
from sqlalchemy.orm import Session
from app.models.models import B2BLead
from app.services.crm_tracker import CRMTrackerService

# Revenue-at-risk stages (active but stalling)
RISK_STAGES = ["SAMPLE_SENT", "PROPOSAL_SENT", "MEETING_BOOKED", "MEETING_COMPLETED"]

def generate_brief(db: Session) -> dict:
    """Build the complete founder daily brief from live DB data."""
    today = date.today()
    all_leads = db.query(B2BLead).all()
    active = [l for l in all_leads if l.status not in ("COLD", "ORDER_WON", "ONBOARDED", "ACCOUNT_GROWTH")]
    
    kpis = CRMTrackerService.get_kpis(db)
    forecasts = kpis.get("forecasts", {})
    leakage = kpis.get("leakage", {})
    reorders = CRMTrackerService.get_reorder_alerts(db)

    # Calculate collection-adjusted metrics
    pipeline_val = kpis["pipeline_value_inr"]
    expected_rev = kpis["expected_revenue_inr"]
    expected_margin = kpis["expected_margin_inr"]
    
    cash_30 = forecasts.get("30", {}).get("cash_collection", 0)
    rev_30 = forecasts.get("30", {}).get("revenue", 0)
    margin_30 = forecasts.get("30", {}).get("margin", 0)
    
    revenue_at_risk = kpis["revenue_at_risk_inr"]
    
    # Reorders due (runout < 15 days)
    reorders_due_value = sum(a["monthly_value"] for a in reorders if a["runout_days"] < 15)
    
    # Leaking revenue & margin
    leaking_rev = leakage.get("total_revenue_leaking", 0)
    leaking_margin = leakage.get("total_margin_leaking", 0)

    # ── Action / Lead insights ────────────────────────────────────────────────
    # Today's Top Action: highest cash velocity score per founder hour
    # We define action time requirements:
    # - Call Distributor: 15 min (0.25h)
    # - Send Sample: 5 min (0.083h)
    # - Negotiate Proposal: 10 min (0.17h)
    # - Resolve Objection: 20 min (0.33h)
    best_action_lead = None
    best_action_title = "Qualify Leads"
    best_action_rev_hour = 0.0
    best_action_rev = 0.0
    best_action_margin = 0.0
    best_action_prob = 0.0
    
    for l in active:
        f_metrics = CRMTrackerService.get_financial_metrics(l)
        status_upper = (l.status or "DISCOVERED").upper()
        
        # Determine time required
        time_req = 0.17 # default 10 min
        action_name = "Follow Up"
        if status_upper == "REPLIED":
            action_name = f"Call {l.company} to Book Meeting"
            time_req = 0.25
        elif status_upper == "MEETING_COMPLETED":
            action_name = f"Send Sample Kit to {l.company}"
            time_req = 0.083
        elif status_upper == "FEEDBACK_PENDING":
            action_name = f"Call {l.company} for Feedback"
            time_req = 0.17
        elif status_upper == "FEEDBACK_RECEIVED":
            action_name = f"Generate Proposal for {l.company}"
            time_req = 0.25
        elif status_upper == "PROPOSAL_SENT":
            action_name = f"Negotiate Proposal with {l.company}"
            time_req = 0.17
        elif l.division == "tender" and status_upper in ("DISCOVERED", "QUALIFIED"):
            action_name = f"Submit Tender Bid for {l.company}"
            time_req = 5.0
            
        rev_per_hour = f_metrics["expected_revenue"] / time_req if time_req > 0 else 0.0
        if rev_per_hour > best_action_rev_hour:
            best_action_rev_hour = rev_per_hour
            best_action_lead = l
            best_action_title = action_name
            best_action_rev = f_metrics["expected_revenue"]
            best_action_margin = f_metrics["expected_margin"]
            best_action_prob = f_metrics["win_probability"]

    # Lead to ignore: lowest Cash Velocity / longest delay (like govt tender)
    lead_to_ignore = None
    lowest_velocity = 99999999.0
    for l in active:
        f_metrics = CRMTrackerService.get_financial_metrics(l)
        if f_metrics["cash_velocity_score"] < lowest_velocity:
            lowest_velocity = f_metrics["cash_velocity_score"]
            lead_to_ignore = l
            
    better_opp_count = 0
    if lead_to_ignore:
        better_opp_count = sum(1 for l in active if CRMTrackerService.get_financial_metrics(l)["cash_velocity_score"] > lowest_velocity)
            
    better_opp_count = 0
    if lead_to_ignore:
        better_opp_count = sum(1 for l in active if CRMTrackerService.get_financial_metrics(l)["cash_velocity_score"] > lowest_velocity)
            
    # Which lead can close fastest: active lead with minimum close_days
    fastest_lead = None
    min_close = 999
    for l in active:
        f_metrics = CRMTrackerService.get_financial_metrics(l)
        if f_metrics["close_days"] < min_close:
            min_close = f_metrics["close_days"]
            fastest_lead = l
            
    # Which customer should reorder today: won account with minimum runout_days
    reorder_today_alert = None
    min_runout = 999
    for a in reorders:
        if a["runout_days"] < min_runout:
            min_runout = a["runout_days"]
            reorder_today_alert = a

    # Format helpers for rupee output
    def fmt_val(n: float) -> str:
        if n >= 10000000.0:
            return f"₹{n/10000000.0:.2f}Cr"
        if n >= 100000.0:
            return f"₹{n/100000.0:.2f}L"
        return f"₹{int(n):,}"

    # Build Q&A daily brief answers
    q1 = f"Total revenue in active pipeline is {fmt_val(pipeline_val)}."
    q2 = f"Expected revenue that can close in next 30 days is {fmt_val(rev_30)} (Expected Margin: {fmt_val(margin_30)})."
    
    biggest_opt = max(active, key=lambda l: l.estimated_value) if active else None
    q3 = f"{biggest_opt.company} ({fmt_val(biggest_opt.estimated_value)})" if biggest_opt else "None"
    
    biggest_risk = max(active, key=lambda l: l.estimated_value if l.status in RISK_STAGES else 0.0) if active else None
    q4 = f"{biggest_risk.company} ({fmt_val(biggest_risk.estimated_value)} at risk)" if biggest_risk else "None"
    
    q5 = [
        f"Call {best_action_lead.company if best_action_lead else 'XYZ Distributor'} (Potential {fmt_val(best_action_rev)})",
        f"Send Sample Kit to {active[0].company if len(active) > 0 else 'ABC Tech'}",
        f"Approve Bid for {active[1].company if len(active) > 1 else 'Railway Tender'}"
    ]
    
    q6 = f"{fmt_val(leaking_rev)} Revenue ({fmt_val(leaking_margin)} Margin) is currently leaking across stalled stages."
    q7 = f"{fastest_lead.company if fastest_lead else 'None'} is expected to close in {min_close} days."
    q8 = f"Deprioritize {lead_to_ignore.company if lead_to_ignore else 'None'} ({better_opp_count} better opportunities exist)."
    q9 = f"{reorder_today_alert['company'] if reorder_today_alert else 'None'} has {min_runout} days of stock remaining."
    q10 = f"{best_action_title} yields {fmt_val(best_action_rev_hour)} expected revenue per founder hour."

    brief = {
        "date": today.strftime("%A, %d %B %Y"),
        "generated_at": datetime.now().strftime("%H:%M"),
        # Financial KPIs
        "pipeline_value_inr": round(pipeline_val),
        "expected_revenue_inr": round(expected_rev),
        "expected_margin_inr": round(expected_margin),
        "cash_expected_30_days": round(cash_30),
        "expected_30_day_revenue": round(rev_30),
        "expected_30_day_margin": round(margin_30),
        "revenue_at_risk_inr": round(revenue_at_risk),
        "reorders_due_inr": round(reorders_due_value),
        "leaking_revenue_inr": round(leaking_rev),
        "leaking_margin_inr": round(leaking_margin),
        # Insights
        "top_action": {
            "company": best_action_lead.company if best_action_lead else "—",
            "contact": best_action_lead.contact_name if best_action_lead else "—",
            "phone": best_action_lead.phone if best_action_lead else "—",
            "title": best_action_title,
            "revenue_unlocked": round(best_action_rev),
            "margin_unlocked": round(best_action_margin),
            "win_probability": round(best_action_prob * 100),
            "revenue_per_hour": round(best_action_rev_hour)
        },
        "deprioritized_opportunity": {
            "company": lead_to_ignore.company if lead_to_ignore else "—",
            "expected_margin": round(CRMTrackerService.get_financial_metrics(lead_to_ignore)["expected_margin"]) if lead_to_ignore else 0,
            "collection_delay": CRMTrackerService.get_financial_metrics(lead_to_ignore)["collection_delay_days"] if lead_to_ignore else 180,
            "cash_velocity": "Low" if lead_to_ignore else "—",
            "reason": f"{better_opp_count} better opportunities exist"
        },
        # Daily brief QA answers
        "q1_pipeline": q1,
        "q2_close_30": q2,
        "q3_biggest_opp": q3,
        "q4_biggest_risk": q4,
        "q5_actions": q5,
        "q6_leaking": q6,
        "q7_fastest_close": q7,
        "q8_deprioritized": q8,
        "q9_reorder": q9,
        "q10_efficiency": q10,
        
        "data_quality": kpis["data_quality"],
        "new_leads_this_week": kpis["leads_discovered"]
    }
    return brief

def send_brief(brief: dict) -> dict:
    """Email the daily brief to Hiten. No-op if SMTP not configured."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    smtp_host = "smtp.zoho.in"
    smtp_port = 587
    sender    = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    password  = os.getenv("ZOHO_APP_PASSWORD", "")
    recipient = "hitenjain.12@gmail.com"

    if not password or password.strip() in ("", "your_zoho_app_password_here"):
        return {"sent": False, "reason": "ZOHO_APP_PASSWORD not configured"}

    def fmt(n: int) -> str:
        if n >= 10_00_000:
            return f"₹{n/10_00_000:.1f}L"
        return f"₹{n:,}"

    ta = brief["top_action"]
    dq = brief["data_quality"]

    text = f"""☕ GOOD MORNING HITEN — YOUR REVENUE OPERATING SYSTEM BRIEF ☕
{brief["date"]} · Generated {brief["generated_at"]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DAILY REVENUE SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pipeline Value:           {fmt(brief["pipeline_value_inr"])}
Expected 30-Day Revenue:  {fmt(brief["expected_30_day_revenue"])}
Expected 30-Day Margin:   {fmt(brief["expected_30_day_margin"])}
Expected 30-Day Cash:     {fmt(brief["cash_expected_30_days"])}
Revenue At Risk:          {fmt(brief["revenue_at_risk_inr"])}
Reorders Due:             {fmt(brief["reorders_due_inr"])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOUNDER'S 10 DAILY QUESTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Q1. How much revenue in active pipeline?
  {brief["q1_pipeline"]}
Q2. How much can close in next 30 days?
  {brief["q2_close_30"]}
Q3. Biggest opportunity?
  {brief["q3_biggest_opp"]}
Q4. Biggest risk?
  {brief["q4_biggest_risk"]}
Q5. What should Hiten do today?
  1. {brief["q5_actions"][0]}
  2. {brief["q5_actions"][1]}
  3. {brief["q5_actions"][2]}
Q6. What revenue is leaking today?
  {brief["q6_leaking"]}
Q7. Which lead can close fastest?
  {brief["q7_fastest_close"]}
Q8. Which lead should be ignored?
  {brief["q8_ignore"]}
Q9. What customer should reorder today?
  {brief["q9_reorder"]}
Q10. Which action creates most revenue/hour?
  {brief["q10_efficiency"]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DATA QUALITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Complete Contacts:    {dq["complete_contacts"]}
Incomplete Contacts:  {dq["incomplete_contacts"]}

Dashboard: http://localhost:3000/dashboard
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Purity Beans AI Revenue OS
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"☕ Purity Beans Revenue OS Brief — {brief['date']}"
    msg["From"]    = f"Purity Beans AI <{sender}>"
    msg["To"]      = recipient
    msg.attach(MIMEText(text, "plain", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.sendmail(sender, recipient, msg.as_string())
        return {"sent": True, "to": recipient}
    except Exception as e:
        return {"sent": False, "reason": str(e)}
