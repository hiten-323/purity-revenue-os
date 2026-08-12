"""
Mass campaign engine — approve thousands, send none of them by accident.

THE ONE RULE THAT MATTERS
Approving is not sending. "Approve Entire Eligible Queue" creates a campaign,
freezes the recipient list, and stops. Nothing leaves until the founder confirms
a second time against a preview showing exactly who is included and who was
excluded and why.

That separation is the whole design. At ten recipients an accidental send is
embarrassing; at three thousand it is a domain burned in an afternoon, which is
precisely how this account got blocked once already.

WHAT IS CHECKED, PER RECIPIENT, TWICE
Once when the campaign is built, and again immediately before each batch, because
a list frozen at 09:00 is a claim about 09:00. Someone may reply, opt out or
bounce at 11:00, and the batch at 12:00 must know.

  trust   VERIFIED / FOUNDER_VERIFIED / REPLIED   — is the address real?
  status  CONTACTABLE / FOLLOW_UP                 — may we write to them?

Both, independently. A REPLIED address that opted out is trustworthy and
off-limits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Batches are small and paced on purpose. The provider block on 22 Jul followed
# 26 emails inside one minute; deliverability.py enforces the per-hour and
# per-day caps, and this layer never tries to outrun them.
DEFAULT_BATCH_SIZE = 50
DEFAULT_BATCH_PAUSE_MINUTES = 30


@dataclass
class Recipient:
    lead_id: int
    company: str
    email: str
    stage: str
    trust: str
    subject: str = ""
    body: str = ""


@dataclass
class Preview:
    campaign_id: str
    name: str
    eligible: int
    excluded: int
    by_stage: dict = field(default_factory=dict)
    exclusions: dict = field(default_factory=dict)
    batch_size: int = DEFAULT_BATCH_SIZE
    estimated_days: float = 0.0
    estimated_hours: float = 0.0
    warnings: list = field(default_factory=list)


def _eligibility(lead) -> tuple[bool, str]:
    """Both gates, independently. Returns (eligible, exclusion reason)."""
    from app.services.contact_trust import sendable, actionable

    ok_status, why_status = actionable(lead)
    if not ok_status:
        return False, f"status: {why_status}"
    ok_trust, why_trust = sendable(lead)
    if not ok_trust:
        return False, f"trust: {why_trust}"
    if (lead.status or "").upper() in ("DISQUALIFIED", "CLOSED_LOST",
                                       "DO_NOT_CONTACT", "ORDER_WON"):
        return False, f"stage: {lead.status}"
    return True, ""


def build(db, city: str = "", region: str = "", category: str = "",
          name: str = "", batch_size: int = DEFAULT_BATCH_SIZE) -> tuple[Preview, list]:
    """
    Assemble a campaign and return the preview. Sends nothing, writes nothing.

    Every recipient gets an individually generated draft here — a campaign of
    three thousand is three thousand drafts, not one template with the company
    name substituted in. A recipient whose draft cannot be generated is excluded
    rather than sent something generic.
    """
    from app.models.models import B2BLead
    from app.services.email_sender import generate_b2b_pitch_email
    from app.services.deliverability import MAX_PER_DAY

    q = db.query(B2BLead)
    if city:
        q = q.filter(B2BLead.city.ilike(f"%{city.strip()}%"))
    if region:
        q = q.filter(B2BLead.region.ilike(f"%{region.strip()}%"))
    if category and category.upper() not in ("ALL", ""):
        q = q.filter(B2BLead.division == category.strip())
    leads = q.all()

    recipients, exclusions, by_stage = [], {}, {}
    for l in leads:
        ok, why = _eligibility(l)
        if not ok:
            key = why.split(":")[0].strip()
            exclusions[key] = exclusions.get(key, 0) + 1
            continue
        try:
            subj, body = generate_b2b_pitch_email(l)
        except Exception:
            exclusions["draft generation failed"] = \
                exclusions.get("draft generation failed", 0) + 1
            continue
        stage = (l.status or "DISCOVERED").upper()
        by_stage[stage] = by_stage.get(stage, 0) + 1
        recipients.append(Recipient(l.id, l.company, l.email, stage,
                                    l.email_trust or "", subj, body))

    cid = f"CAMP-{(city or region or 'ALL')[:8].upper().replace(' ', '')}-" \
          f"{datetime.utcnow():%Y%m%d-%H%M}"
    days = (len(recipients) / MAX_PER_DAY) if recipients else 0.0

    warnings = []
    if not recipients:
        warnings.append(
            "No eligible recipients. Every business was excluded — see the "
            "exclusion breakdown. Nothing will be sent.")
    if days > 1:
        warnings.append(
            f"At the {MAX_PER_DAY}/day cap this campaign takes {days:.1f} days. "
            f"The cap exists because this domain was blocked once for sending "
            f"26 emails in a minute.")

    return Preview(
        campaign_id=cid,
        name=name or f"{city or region or 'All'} campaign",
        eligible=len(recipients),
        excluded=len(leads) - len(recipients),
        by_stage=dict(sorted(by_stage.items(), key=lambda kv: -kv[1])),
        exclusions=dict(sorted(exclusions.items(), key=lambda kv: -kv[1])),
        batch_size=batch_size,
        estimated_days=round(days, 1),
        estimated_hours=round(len(recipients) / max(1, batch_size)
                              * (DEFAULT_BATCH_PAUSE_MINUTES / 60.0), 1),
        warnings=warnings,
    ), recipients


def freeze(db, preview: Preview, recipients: list) -> dict:
    """
    Persist the campaign and lock the recipient list. Still sends nothing.

    The list is frozen so that CRM churn between approval and delivery cannot
    quietly add anyone. Removal is always allowed — a reply or an opt-out after
    freezing must still take effect, and does, because every batch re-checks.
    """
    from app.models.models import WorkflowEvent

    db.add(WorkflowEvent(
        event_type="CAMPAIGN_CREATED", actor="FOUNDER", channel="campaign",
        payload={"campaign_id": preview.campaign_id, "name": preview.name,
                 "recipients": [r.lead_id for r in recipients],
                 "eligible": preview.eligible, "excluded": preview.excluded,
                 "by_stage": preview.by_stage, "exclusions": preview.exclusions,
                 "batch_size": preview.batch_size,
                 "status": "AWAITING_CONFIRMATION",
                 "note": "recipients frozen — nothing sends until the founder "
                         "confirms this campaign id"},
        occurred_at=datetime.utcnow()))
    for r in recipients:
        db.add(WorkflowEvent(
            lead_id=r.lead_id, event_type="CAMPAIGN_ENQUEUED", actor="FOUNDER",
            channel="campaign",
            payload={"campaign_id": preview.campaign_id, "subject": r.subject},
            occurred_at=datetime.utcnow()))
    db.commit()
    return {"campaign_id": preview.campaign_id, "status": "AWAITING_CONFIRMATION",
            "recipients_frozen": len(recipients),
            "next_step": "POST /b2b/campaign/{campaign_id}/confirm to begin delivery"}


def next_batch(db, campaign_id: str, limit: int | None = None) -> dict:
    """
    The next batch to send, re-validated at this moment.

    Re-checking is not belt-and-braces; it is the only thing that makes reply
    suppression real. A recipient who replied an hour ago is dropped here, which
    is what stops a generic sequence talking over a live conversation.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.deliverability import check_send_allowed

    created = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type == "CAMPAIGN_CREATED").all()
    camp = next((e for e in created
                 if (e.payload or {}).get("campaign_id") == campaign_id), None)
    if camp is None:
        return {"error": f"unknown campaign {campaign_id}"}
    p = camp.payload or {}
    if p.get("status") != "CONFIRMED":
        return {"campaign_id": campaign_id, "blocked": True,
                "reason": f"campaign is {p.get('status')} — the founder has not "
                          f"confirmed delivery"}

    verdict = check_send_allowed(db)
    if not verdict.allowed:
        return {"campaign_id": campaign_id, "blocked": True,
                "reason": verdict.reason,
                "retry_after_seconds": verdict.retry_after_seconds}

    already = {e.lead_id for e in db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(["EMAIL_SENT", "CAMPAIGN_RECIPIENT_DROPPED"])
    ).all() if e.lead_id}
    replied = {e.lead_id for e in db.query(WorkflowEvent).all()
               if e.lead_id and "REPLI" in (e.event_type or "")}

    size = limit or p.get("batch_size") or DEFAULT_BATCH_SIZE
    batch, dropped = [], []
    for lid in p.get("recipients", []):
        if len(batch) >= size:
            break
        if lid in already:
            continue
        lead = db.query(B2BLead).filter(B2BLead.id == lid).first()
        if lead is None:
            continue
        if lid in replied:
            dropped.append({"lead_id": lid, "reason": "replied — moved to the "
                                                      "follow-up workflow"})
            continue
        ok, why = _eligibility(lead)
        if not ok:
            dropped.append({"lead_id": lid, "reason": why})
            continue
        batch.append({"lead_id": lid, "company": lead.company,
                      "email": lead.email})

    for d in dropped:
        db.add(WorkflowEvent(
            lead_id=d["lead_id"], event_type="CAMPAIGN_RECIPIENT_DROPPED",
            actor="SYSTEM", channel="campaign",
            payload={"campaign_id": campaign_id, "reason": d["reason"]},
            occurred_at=datetime.utcnow()))
    if dropped:
        db.commit()

    return {"campaign_id": campaign_id, "batch": batch, "batch_size": len(batch),
            "dropped_this_pass": dropped,
            "note": "re-validated at this moment — repliers and opt-outs since "
                    "freezing are removed here"}
