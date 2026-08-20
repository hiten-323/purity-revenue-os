"""
AI Email Drafter — writes human-sounding follow-up emails for B2B leads.
Uses Cerebras LLM when available; falls back to smart templates.
All drafts are saved for Hiten's approval before any email is sent.
"""
from __future__ import annotations
import os, json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models.models import B2BLead, EmailDraft

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")

# ── Follow-up type triggers ───────────────────────────────────────────────────

FOLLOW_UP_RULES = [
    {
        "type": "nudge",
        "label": "Day-5 No-Reply Nudge",
        "statuses": ["EMAIL_SENT"],
        "min_days_since_contact": 5,
        "reason_template": "No reply to initial pitch for {days} days",
    },
    {
        "type": "sample_push",
        "label": "Sample Offer Follow-up",
        "statuses": ["REPLIED"],
        "min_days_since_contact": 3,
        "reason_template": "Replied but hasn't requested sample yet ({days} days)",
    },
    {
        "type": "post_sample",
        "label": "Post-Sample Feedback Request",
        "statuses": ["SAMPLE_SENT"],
        "min_days_since_contact": 7,
        "reason_template": "Sample sent {days} days ago — asking for feedback",
    },
    {
        "type": "meeting_book",
        "label": "Meeting Booking Request",
        "statuses": ["PROPOSAL_SENT"],
        "min_days_since_contact": 4,
        "reason_template": "Proposal sent {days} days ago — pushing for a call",
    },
    {
        "type": "re_engage",
        "label": "Re-engagement (Went Cold)",
        "statuses": ["COLD"],
        "min_days_since_contact": 30,
        "reason_template": "Went cold {days} days ago — re-engagement attempt",
    },
]


def _days_since(dt: datetime | None) -> int:
    if not dt:
        return 999
    return (datetime.utcnow() - dt).days


def _llm_write_email(lead: B2BLead, follow_up_type: str, context: str) -> tuple[str, str]:
    """Call Cerebras to write a human email. Returns (subject, body)."""
    try:
        import httpx
        prompt = f"""You are writing a B2B sales follow-up email on behalf of Hiten Jain, Founder of Purity Beans (Pure Pantry Provisions), a premium 100% pure coffee brand with no chicory or additives. FSSAI licensed, MSME registered.

Lead details:
- Company: {lead.company}
- Contact: {lead.contact_name or 'the procurement team'}
- Title: {lead.contact_title or 'Decision Maker'}
- Industry: {lead.industry}
- City: {lead.city}
- Division: {lead.division}
- Current Status: {lead.status}
- Estimated Value: ₹{lead.estimated_value:,.0f}/year

Follow-up type: {follow_up_type}
Context: {context}

Write a SHORT, HUMAN, conversational follow-up email. Rules:
1. Sound like a real person, not a robot or template
2. Reference their specific company/industry naturally
3. Maximum 120 words in the body
4. No bullet lists — flowing sentences
5. Warm but professional tone — like a founder reaching out personally
6. End with ONE specific call to action
7. Do NOT include a signature (it's added automatically)

Respond ONLY with valid JSON:
{{"subject": "...", "body": "..."}}"""

        resp = httpx.post(
            "https://api.cerebras.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 400},
            timeout=15,
        )
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content)
        return parsed["subject"], parsed["body"]
    except Exception as e:
        # Falling through to a template is the right behaviour — a draft still
        # gets written and the founder still decides. What was wrong was doing
        # it in silence: a rejected API key (the live one currently returns
        # {"code":"wrong_api_key"}) is indistinguishable from a one-off timeout,
        # so every email quietly became a template and nothing said so.
        #
        # An auth failure is a CONFIGURATION fault that will not fix itself, so
        # it is named separately from a transient one.
        detail = e.__class__.__name__
        try:
            if isinstance(e, KeyError):
                detail = f"unexpected response shape (missing {e})"
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (401, 403):
                detail = f"AUTH REJECTED ({code}) — CEREBRAS_API_KEY is invalid"
        except Exception:
            pass
        print(f"[email_drafter] Cerebras unavailable, using template: {detail}")
        _LLM_FAILURES.append(detail)
        return None, None


# Why a module-level list: the drafter is called in a loop by the worker, and a
# per-call print scrolls past. This lets /health and the draft response report
# "N drafts fell back to templates because the key is rejected" instead of the
# founder discovering it from the tone of the emails.
_LLM_FAILURES: list[str] = []


def llm_status() -> dict:
    """Whether LLM drafting is actually working, for /health and the UI."""
    return {
        "configured": bool(CEREBRAS_API_KEY),
        "recent_failures": len(_LLM_FAILURES),
        "last_failure": _LLM_FAILURES[-1] if _LLM_FAILURES else None,
        "degraded": bool(_LLM_FAILURES),
        "note": ("drafts are falling back to templates — they still require "
                 "founder approval, but they are not LLM-written")
        if _LLM_FAILURES else "ok",
    }


# ── Smart template fallbacks (used when LLM unavailable) ─────────────────────

def _template_email(lead: B2BLead, follow_up_type: str) -> tuple[str, str]:
    first = (lead.contact_name or "").split()[0] if lead.contact_name else "there"
    # Customer-facing name, never the raw directory string. Nine subject
    # templates across four modules interpolate this, and one produced
    # "A sample for WrkPod | Coworking Space in Coimbatore | Shared Office
    # Space?" as a live subject. Cleaning at each derivation keeps the raw
    # value on the record for provenance while nothing customer-facing sees it.
    from app.services.lead_quality import display_name
    company = display_name(lead.company or "")
    city = lead.city or "your city"

    if follow_up_type == "nudge":
        subject = f"Quick follow-up — Purity Beans for {company}"
        body = f"""Hi {first},

I hope this finds you well. I reached out last week about Purity Beans — India's premium 100% pure coffee with no chicory or additives.

I know procurement decisions take time and inboxes get busy. I just wanted to make sure my previous email didn't slip through the cracks.

Would it make sense to send across a complimentary tasting kit for your team in {city}? No strings attached — just genuine coffee that speaks for itself.

Happy to answer any questions if you'd like to reply here."""

    elif follow_up_type == "sample_push":
        subject = f"Your complimentary coffee sample — {company}"
        body = f"""Hi {first},

Thank you for your response — really appreciated it.

I'd love to send across a complimentary tasting kit so your team can experience the difference firsthand. We have four variants: Purica, Bold, Ultra Blend, and Purista — all 100% coffee, no chicory.

Just confirm your office address in {city} and I'll arrange dispatch within 24 hours. No commitment needed at this stage."""

    elif follow_up_type == "post_sample":
        subject = f"How did the Purity Beans sample go? — {company}"
        body = f"""Hi {first},

It's been about a week since I sent the tasting kit across to {company} and I was hoping to hear back from you.

Would love to know what your team thought — did the taste profile work for your pantry? Any feedback, even critical, helps us ensure we're the right fit.

If you're happy with the sample, I can put together a customised pricing proposal for your monthly volumes in {city}."""

    elif follow_up_type == "meeting_book":
        subject = f"15-min call this week? — {company} coffee supply"
        body = f"""Hi {first},

I sent across a detailed proposal last week and wanted to follow up personally.

Would you have 15 minutes this week for a quick call? I can walk you through the pricing, answer any questions about supply chain, and we can figure out if this makes sense for {company}.

Happy to work around your schedule — just suggest a time that works."""

    else:  # re_engage
        subject = f"Checking in — Purity Beans x {company}"
        body = f"""Hi {first},

It's been a while since we last connected and I didn't want to lose touch entirely.

A lot has changed at Purity Beans — we've expanded our range, tightened our pricing for institutional buyers, and improved dispatch timelines across India.

If coffee supply is something {company} revisits this quarter, I'd be happy to reconnect. Even a brief 10-minute conversation would help me understand if there's a fit."""

    return subject, body


# ── Main drafting function ────────────────────────────────────────────────────

def generate_followup_drafts(db: Session) -> list[dict]:
    """
    Scan leads, identify which need follow-up, write AI emails, save as EmailDraft.
    Returns list of draft dicts created this run.
    """
    created = []

    for rule in FOLLOW_UP_RULES:
        leads = db.query(B2BLead).filter(
            B2BLead.status.in_(rule["statuses"]),
            B2BLead.email.like("%@%"),
        ).all()

        for lead in leads:
            days = _days_since(lead.email_sequence_last_sent or lead.last_updated)
            if days < rule["min_days_since_contact"]:
                continue

            # Don't create duplicate pending drafts for same lead + type
            existing = db.query(EmailDraft).filter(
                EmailDraft.lead_id == lead.id,
                EmailDraft.follow_up_type == rule["type"],
                EmailDraft.status == "PENDING",
            ).first()
            if existing:
                continue

            reason = rule["reason_template"].format(days=days)
            context = f"Lead status: {lead.status}. Days since last contact: {days}. Industry: {lead.industry}. Division: {lead.division}."

            # Try LLM first, fall back to template
            subject, body = (None, None)
            if CEREBRAS_API_KEY:
                subject, body = _llm_write_email(lead, rule["type"], context)
            if not subject:
                subject, body = _template_email(lead, rule["type"])

            draft = EmailDraft(
                lead_id=lead.id,
                follow_up_type=rule["type"],
                subject=subject,
                body=body,
                reason=reason,
                status="PENDING",
            )
            db.add(draft)
            db.flush()

            created.append({
                "draft_id": draft.id,
                "lead": lead.company,
                "type": rule["type"],
                "reason": reason,
            })

    db.commit()
    return created
