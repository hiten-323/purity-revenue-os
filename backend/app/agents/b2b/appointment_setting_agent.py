"""
Appointment Setting Agent — books meetings for qualified B2B leads.

Flow per lead:
  Qualified Lead → Generate meeting request email + WhatsApp + calendar link
               → Track response → Escalate if no reply in 48h
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class MeetingSlot:
    date: str          # "2026-06-18"
    time: str          # "11:00 AM IST"
    duration_min: int  # 20


@dataclass
class AppointmentRequest:
    lead_id: str
    company: str
    contact_name: str
    contact_title: str
    phone: str
    email: str
    division: Literal["distributor", "corporate", "wholesale", "retail", "gifting"]
    estimated_value_inr: int
    qualification_notes: str
    slots: list[MeetingSlot] = field(default_factory=list)


@dataclass
class AppointmentResult:
    lead_id: str
    company: str
    status: Literal["email_sent", "whatsapp_sent", "booked", "escalated", "cold"]
    meeting_email: str
    whatsapp_message: str
    calendar_link: str
    followup_date: str
    priority: Literal["HOT", "WARM", "COLD"]


class AppointmentSettingAgent:
    """
    Generates personalised meeting-request materials for each qualified lead
    and tracks appointment funnel: sent → booked → no-show → re-engage.
    """

    DIVISION_PITCH = {
        "distributor": "become Purity Beans' regional distribution partner and earn ₹{margin}/case margin",
        "corporate":   "set up Purity Beans' premium pantry coffee program for your {count} employees",
        "wholesale":   "access Purity Beans' wholesale pricing and bulk supply program",
        "retail":      "stock Purity Beans on your premium shelf alongside top FMCG brands",
        "gifting":     "include Purity Beans in your corporate gifting hampers this festive season",
    }

    def generate_meeting_request(self, req: AppointmentRequest) -> AppointmentResult:
        slots_text = self._format_slots(req.slots or self._default_slots())
        pitch = self.DIVISION_PITCH.get(req.division, "explore a Purity Beans partnership").format(
            margin="2,500", count="your"
        )
        followup = (datetime.today() + timedelta(days=2)).strftime("%Y-%m-%d")

        email = self._build_email(req, pitch, slots_text)
        whatsapp = self._build_whatsapp(req, pitch)
        cal_link = self._build_calendar_link(req)
        priority = self._priority(req.estimated_value_inr)

        return AppointmentResult(
            lead_id=req.lead_id,
            company=req.company,
            status="email_sent",
            meeting_email=email,
            whatsapp_message=whatsapp,
            calendar_link=cal_link,
            followup_date=followup,
            priority=priority,
        )

    def generate_batch(self, leads: list[AppointmentRequest]) -> list[AppointmentResult]:
        results = [self.generate_meeting_request(r) for r in leads]
        results.sort(key=lambda x: {"HOT": 0, "WARM": 1, "COLD": 2}[x.priority])
        return results

    def escalation_check(self, results: list[AppointmentResult], days_since_sent: int) -> list[AppointmentResult]:
        """Mark leads as escalated if no response after threshold."""
        for r in results:
            if days_since_sent >= 2 and r.status == "email_sent" and r.priority == "HOT":
                r.status = "escalated"
            elif days_since_sent >= 4 and r.status == "email_sent":
                r.status = "escalated"
        return results

    # ── Private helpers ──────────────────────────────────────────

    def _build_email(self, req: AppointmentRequest, pitch: str, slots: str) -> str:
        return f"""Subject: Purity Beans — Quick 20-Min Call This Week, {req.contact_name}?

Dear {req.contact_name},

I'm reaching out from Purity Beans, a premium Indian instant coffee brand rapidly growing across B2B channels.

I'd love to {pitch}.

{req.qualification_notes}

Would you have 20 minutes this week? Here are a few slots:
{slots}

Or pick a time that works for you: {self._build_calendar_link(req)}

Best regards,
Purity Beans Sales Team
📞 Reply to this email or WhatsApp us — we respond within 2 hours."""

    def _build_whatsapp(self, req: AppointmentRequest, pitch: str) -> str:
        return (
            f"Hi {req.contact_name}, this is Purity Beans ☕ "
            f"We'd love to {pitch}. "
            f"Can we connect for 20 mins this week? "
            f"Reply YES and we'll share slots 🙏"
        )

    def _build_calendar_link(self, req: AppointmentRequest) -> str:
        # Generates a Calendly-style link placeholder — replace with real Calendly URL
        slug = req.company.lower().replace(" ", "-")[:20]
        return f"https://calendly.com/puritybeans/{slug}-{req.lead_id[:6]}"

    def _format_slots(self, slots: list[MeetingSlot]) -> str:
        lines = []
        for s in slots:
            lines.append(f"  • {s.date} at {s.time} ({s.duration_min} min)")
        return "\n".join(lines)

    def _default_slots(self) -> list[MeetingSlot]:
        base = datetime.today()
        return [
            MeetingSlot((base + timedelta(days=1)).strftime("%A %d %b"), "11:00 AM IST", 20),
            MeetingSlot((base + timedelta(days=2)).strftime("%A %d %b"), "3:00 PM IST",  20),
            MeetingSlot((base + timedelta(days=3)).strftime("%A %d %b"), "10:00 AM IST", 20),
        ]

    def _priority(self, value: int) -> Literal["HOT", "WARM", "COLD"]:
        if value >= 500_000:
            return "HOT"
        if value >= 100_000:
            return "WARM"
        return "COLD"

    def daily_report(self, results: list[AppointmentResult]) -> str:
        hot   = [r for r in results if r.priority == "HOT"]
        warm  = [r for r in results if r.priority == "WARM"]
        esc   = [r for r in results if r.status == "escalated"]
        booked = [r for r in results if r.status == "booked"]

        lines = [
            "═" * 50,
            "APPOINTMENT SETTING DAILY REPORT",
            f"Date: {datetime.today().strftime('%d %b %Y')}",
            "═" * 50,
            f"Total meeting requests sent : {len(results)}",
            f"Meetings booked             : {len(booked)}",
            f"HOT leads (follow up today) : {len(hot)}",
            f"Escalated (no reply 48h+)   : {len(esc)}",
            "",
            "HOT LEADS — CALL TODAY:",
        ]
        for r in hot:
            lines.append(f"  📞 {r.company} | {r.status} | {r.calendar_link}")

        lines += ["", "BOOKED MEETINGS:"]
        for r in booked:
            lines.append(f"  ✅ {r.company} | followup: {r.followup_date}")

        lines += ["", "ESCALATED — NO REPLY:"]
        for r in esc:
            lines.append(f"  🔴 {r.company} — send WhatsApp now")

        lines.append("═" * 50)
        return "\n".join(lines)
