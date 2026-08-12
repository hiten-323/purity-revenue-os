"""
Sampling Workflow Agent — manages the full coffee sample lifecycle.

Workflow:
  Lead Qualified
      ↓
  Sample Dispatched  (generate dispatch note + tracking)
      ↓
  Delivery Confirmed (Day 0)
      ↓
  Day 2: Taste check WhatsApp
      ↓
  Day 5: Feedback call script
      ↓
  Day 7: Meeting / order request
      ↓
  Converted / Re-nurture
"""
from __future__ import annotations
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SampleRequest:
    lead_id: str
    company: str
    contact_name: str
    phone: str
    email: str
    address: str
    city: str
    pin: str
    division: str
    estimated_value_inr: int
    skus_to_send: list[str] = field(default_factory=lambda: [
        "Purica",
        "Bold",
    ])


@dataclass
class SampleRecord:
    lead_id: str
    company: str
    contact_name: str
    phone: str
    dispatch_date: str
    expected_delivery: str
    skus: list[str]
    day2_whatsapp: str
    day5_call_script: str
    day7_meeting_email: str
    tracking_id: str
    status: Literal["pending", "dispatched", "delivered", "feedback_sent", "meeting_requested", "converted", "cold"]
    next_action_date: str
    next_action: str


class SamplingWorkflowAgent:
    """
    Generates all communication and tracking for the Purity Beans sample program.
    One sample → 4 automated touchpoints → meeting booked or re-nurture triggered.
    """

    SKU_DESCRIPTIONS = {
        "Purica":      "smooth, balanced — perfect for office pantries",
        "Bold":        "bold, rich — best for serious coffee drinkers",
        "Ultra Blend": "premium freeze-dried blend — popular for corporate gifting",
        "Purista":     "premium, low-caffeine gourmet Robusta",
    }

    def create_sample_record(self, req: SampleRequest) -> SampleRecord:
        today = datetime.today()
        dispatch = today.strftime("%Y-%m-%d")
        delivery = (today + timedelta(days=3)).strftime("%Y-%m-%d")
        day2 = (today + timedelta(days=5)).strftime("%Y-%m-%d")   # 2 days after delivery
        day5 = (today + timedelta(days=8)).strftime("%Y-%m-%d")
        day7 = (today + timedelta(days=10)).strftime("%Y-%m-%d")
        tracking_id = f"PB-SMPL-{req.lead_id[:6].upper()}-{today.strftime('%d%m')}"

        return SampleRecord(
            lead_id=req.lead_id,
            company=req.company,
            contact_name=req.contact_name,
            phone=req.phone,
            dispatch_date=dispatch,
            expected_delivery=delivery,
            skus=req.skus_to_send,
            day2_whatsapp=self._day2_whatsapp(req, delivery),
            day5_call_script=self._day5_call_script(req),
            day7_meeting_email=self._day7_email(req),
            tracking_id=tracking_id,
            status="dispatched",
            next_action_date=day2,
            next_action="Send Day-2 taste check WhatsApp",
        )

    def dispatch_note(self, req: SampleRequest, tracking_id: str) -> str:
        sku_list = "\n".join(f"  • {s}" for s in req.skus_to_send)
        return f"""
PURITY BEANS — SAMPLE DISPATCH NOTE
Tracking ID : {tracking_id}
Date        : {datetime.today().strftime('%d %b %Y')}

TO:
  {req.contact_name}
  {req.company}
  {req.address}
  {req.city} — {req.pin}
  📞 {req.phone}

SAMPLES INCLUDED:
{sku_list}

NOTE: Premium instant coffee samples. No invoice enclosed.
      For partnership enquiries: sales@puritybeans.in
"""

    def advance_status(self, record: SampleRecord) -> SampleRecord:
        """Move record to next stage based on current status."""
        flow = {
            "dispatched": ("delivered",          "Send Day-2 taste check WhatsApp"),
            "delivered":  ("feedback_sent",       "Make Day-5 feedback call"),
            "feedback_sent": ("meeting_requested","Send Day-7 meeting email"),
            "meeting_requested": ("converted",    "Follow up in 3 days if no reply"),
        }
        if record.status in flow:
            record.status, record.next_action = flow[record.status]
            record.next_action_date = (datetime.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        return record

    def daily_sampling_report(self, records: list[SampleRecord]) -> str:
        by_status: dict[str, list[SampleRecord]] = {}
        for r in records:
            by_status.setdefault(r.status, []).append(r)

        lines = [
            "═" * 55,
            "PURITY BEANS — SAMPLING PIPELINE REPORT",
            f"Date: {datetime.today().strftime('%d %b %Y')}",
            "═" * 55,
            f"Total samples in pipeline : {len(records)}",
            f"Converted to orders       : {len(by_status.get('converted', []))}",
            f"Meeting requested         : {len(by_status.get('meeting_requested', []))}",
            f"Awaiting feedback         : {len(by_status.get('feedback_sent', []))}",
            f"In transit                : {len(by_status.get('dispatched', []))}",
            "",
        ]

        action_today = [r for r in records if r.next_action_date <= datetime.today().strftime("%Y-%m-%d")]
        lines.append(f"ACTION NEEDED TODAY ({len(action_today)} leads):")
        for r in action_today:
            lines.append(f"  ▶ {r.company} | {r.status} | {r.next_action} | 📞 {r.phone}")

        lines += ["", "CONVERTED THIS WEEK:"]
        for r in by_status.get("converted", []):
            lines.append(f"  ✅ {r.company}")

        lines.append("═" * 55)
        return "\n".join(lines)

    # ── Message templates ─────────────────────────────────────────

    def _day2_whatsapp(self, req: SampleRequest, delivery_date: str) -> str:
        sku_names = " and ".join(s.split(" 50g")[0] for s in req.skus_to_send)
        return (
            f"Hi {req.contact_name} ☕ This is Purity Beans! "
            f"Your {sku_names} samples should have arrived. "
            f"Did you get a chance to try them? We'd love to know what you thought! "
            f"Reply here or call us anytime 🙏"
        )

    def _day5_call_script(self, req: SampleRequest) -> str:
        sku_list = ", ".join(req.skus_to_send)
        return f"""DAY-5 CALL SCRIPT — {req.company}
Contact : {req.contact_name} | 📞 {req.phone}

OPENING:
"Hi {req.contact_name}, this is [Your Name] from Purity Beans.
I'm calling to check if you received our samples — {sku_list}?"

IF YES, TRIED:
"Wonderful! What did you and your team think?
[Listen → note feedback]
Based on your setup, I think the [Purica/Bold] would be a great fit.
Could we have a quick 20-minute call to discuss volumes and pricing?"

IF YES, NOT TRIED:
"No problem at all! Which day works for you to try it?
I'll follow up on [specific date]. Is that okay?"

IF NOT RECEIVED:
"I'm so sorry — let me check the tracking right now.
Tracking ID: {req.lead_id[:6].upper()}
I'll resend immediately. Is this address correct? {req.address}"

CLOSE:
"Can I send you a calendar invite for a 20-minute call on [Day]?
I'd love to share our pricing and how other companies in {req.city} are using Purity Beans."
"""

    def _day7_email(self, req: SampleRequest) -> str:
        return f"""Subject: Purity Beans Samples — Ready to Move Forward, {req.contact_name}?

Hi {req.contact_name},

I hope you enjoyed the Purity Beans samples we sent over!

Many of our B2B partners told us the same thing after their first taste:
"This is much better than what we've been serving."

I'd love to have a quick 20-minute call to discuss:
✓ Pricing for your volume ({req.city}-based supply)
✓ Trial order terms (no minimum for first order)
✓ How we handle delivery and replenishment

Would any of these times work for you?
• Tomorrow at 11 AM
• Day after at 3 PM
• Pick your slot: https://calendly.com/puritybeans

Looking forward to brewing a great partnership ☕

Warm regards,
Purity Beans B2B Team
"""
