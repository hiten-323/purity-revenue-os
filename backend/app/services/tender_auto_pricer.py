"""
Tender Auto-Pricer — runs daily.

Logic:
  Day 0–3   after PROPOSAL_SENT  → no change, wait
  Day 4–6   → add 3% discount, nudge email
  Day 7–10  → add 5% more (total 8%), flag URGENT
  Day 11–14 → add 4% more (total 12%), last-chance email
  Day 15+   → cap at 15% discount, mark HIGH priority, alert founder

  Max discount cap: 15% (below this we still make healthy margin).
  Each escalation regenerates proposal_text and recommended_action.
  Only applies to TENDER division leads in PROPOSAL_SENT stage.
"""
from __future__ import annotations
import os, smtplib, json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.database.database import SessionLocal
from app.models.models import B2BLead

# Pricing tiers — (days_since_proposal, extra_discount_pct, label)
ESCALATION_TIERS = [
    (15, 15.0, "FINAL_OFFER"),
    (11, 12.0, "LAST_CHANCE"),
    (7,   8.0, "URGENT"),
    (4,   3.0, "FOLLOW_UP"),
    (0,   0.0, "WAITING"),
]

# Purity Beans base wholesale prices per kg (cost ~Rs 600/kg)
BASE_PRICE_PER_KG = {
    "Bold":    800.0,   # Robusta
    "Purista": 900.0,   # Gourmet Robusta
    "Purica":  950.0,   # Arabica
    "Ultra Blend": 1050.0,  # Premium Blend
    "default": 850.0,
}
MAX_DISCOUNT_PCT = 15.0


def _days_since(dt: datetime) -> int:
    return (datetime.utcnow() - dt).days


def _get_tier(days: int) -> tuple[float, str]:
    for threshold, discount, label in ESCALATION_TIERS:
        if days >= threshold:
            return discount, label
    return 0.0, "WAITING"


def _build_proposal_text(lead: B2BLead, discount_pct: float, tier_label: str) -> str:
    product = "Bold" if lead.proposal_monthly_kg and lead.proposal_monthly_kg < 20 else "Purista"
    base = BASE_PRICE_PER_KG.get(product, BASE_PRICE_PER_KG["default"])
    discounted = round(base * (1 - discount_pct / 100), 2)
    monthly_kg = lead.proposal_monthly_kg or 10.0
    monthly_value = round(discounted * monthly_kg, 2)
    annual_value = round(monthly_value * 12, 2)

    urgency_line = {
        "FINAL_OFFER":  "This is our final offer — pricing valid for 48 hours only.",
        "LAST_CHANCE":  "Last chance to lock in this rate before it reverts to standard pricing.",
        "URGENT":       "We have reserved stock at this rate until end of week.",
        "FOLLOW_UP":    "We wanted to follow up on our proposal and offer an additional saving.",
        "WAITING":      "",
    }.get(tier_label, "")

    return f"""REVISED TENDER PROPOSAL — Purity Beans (Pure Pantry Provisions)

Organisation: {lead.company}
City: {lead.city}
Contact: {lead.contact_name or 'Procurement Manager'}

Product: Purity Beans {product} Instant Coffee (100% Natural)
Volume: {monthly_kg} kg/month
Base Rate: Rs {base:.0f}/kg
Discount Applied: {discount_pct:.0f}%
Discounted Rate: Rs {discounted:.2f}/kg
Monthly Value: Rs {monthly_value:,.0f}
Annual Contract Value: Rs {annual_value:,.0f}

Why Purity Beans:
- No artificial flavours, no chicory — pure coffee
- Free tasting kit dispatched before order commitment
- GeM-registered supplier (UDYAM-PB-XXXX)
- 7-day delivery guarantee across Punjab/Haryana
- Flexible payment: 30-day credit on orders above Rs 10,000/month

{urgency_line}

To accept: reply to this email or call +91-XXXXXXXXXX
Hiten Jain | Pure Pantry Provisions
connect@purepantryprovisions.com
"""


def _send_nudge_email(lead: B2BLead, discount_pct: float, tier_label: str, proposal_text: str):
    sender = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    password = os.getenv("ZOHO_APP_PASSWORD", "")
    if not password or not lead.email:
        return False, "No SMTP password or lead email"

    subject_map = {
        "FOLLOW_UP":   f"Revised pricing for {lead.company} — {discount_pct:.0f}% off",
        "URGENT":      f"Reserved stock offer for {lead.company} — {discount_pct:.0f}% discount expiring",
        "LAST_CHANCE": f"Last chance: {discount_pct:.0f}% off coffee supply — {lead.company}",
        "FINAL_OFFER": f"Final offer: {discount_pct:.0f}% discount — {lead.company} [48 hrs]",
    }
    subject = subject_map.get(tier_label, f"Follow-up: Coffee supply proposal — {lead.company}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Hiten Jain | Pure Pantry Provisions <{sender}>"
    msg["To"] = lead.email
    msg.attach(MIMEText(proposal_text, "plain"))

    try:
        with smtplib.SMTP("smtp.zoho.in", 587) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, lead.email, msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, str(e)


def run_tender_auto_pricer(send_emails: bool = True) -> dict:
    """
    Main entry point. Call daily.
    Returns a report dict with actions taken.
    """
    db = SessionLocal()
    report = {
        "run_at": datetime.utcnow().isoformat(),
        "leads_checked": 0,
        "leads_escalated": 0,
        "emails_sent": 0,
        "actions": [],
    }

    try:
        tender_leads = db.query(B2BLead).filter(
            B2BLead.division == "tender",
            B2BLead.status == "PROPOSAL_SENT",
        ).all()

        report["leads_checked"] = len(tender_leads)

        for lead in tender_leads:
            days = _days_since(lead.last_updated)
            new_discount, tier_label = _get_tier(days)

            if tier_label == "WAITING":
                report["actions"].append({
                    "company": lead.company,
                    "days_since_proposal": days,
                    "action": "waiting — no escalation yet",
                })
                continue

            # Only escalate if discount is increasing
            current_discount = lead.proposal_discount_percent or 0.0
            if new_discount <= current_discount:
                report["actions"].append({
                    "company": lead.company,
                    "days_since_proposal": days,
                    "action": f"already at {current_discount}% — no change",
                })
                continue

            capped_discount = min(new_discount, MAX_DISCOUNT_PCT)
            proposal_text = _build_proposal_text(lead, capped_discount, tier_label)

            # Update lead in CRM
            lead.proposal_discount_percent = capped_discount
            lead.proposal_text = proposal_text
            lead.priority = "HIGH" if tier_label in ("URGENT", "LAST_CHANCE", "FINAL_OFFER") else lead.priority
            lead.recommended_action = (
                f"[AUTO-PRICED {tier_label}] Discount now {capped_discount}% — "
                f"{'Email sent' if send_emails and lead.email else 'No email — contact manually'}. "
                f"Day {days} since proposal."
            )
            lead.last_updated = datetime.utcnow()
            db.commit()

            action_entry = {
                "company": lead.company,
                "city": lead.city,
                "days_since_proposal": days,
                "tier": tier_label,
                "old_discount_pct": current_discount,
                "new_discount_pct": capped_discount,
                "email": lead.email or "none",
                "email_sent": False,
                "email_error": None,
            }

            if send_emails and lead.email:
                ok, msg = _send_nudge_email(lead, capped_discount, tier_label, proposal_text)
                action_entry["email_sent"] = ok
                action_entry["email_error"] = None if ok else msg
                if ok:
                    report["emails_sent"] += 1

            report["leads_escalated"] += 1
            report["actions"].append(action_entry)

    finally:
        db.close()

    return report
