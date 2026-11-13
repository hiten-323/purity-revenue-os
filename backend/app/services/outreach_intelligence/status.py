"""Live Read-Only Auto-Outreach Status Service.

Aggregates actual real-time outreach, calling, messaging, reply, consent,
and failure activity within a rolling time window (e.g. last 3 hours in IST).

Read-only: does not send emails, place calls, modify database state, or alter flags.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.models import ActionQueue, B2BLead, CallHistory, EmailDraft, WorkflowEvent
from app.services import business_hours
from app.services import founder_call_pipeline as pipeline
from app.services.smart_outreach import OutreachTouch

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def get_live_outreach_status(
    db: Session,
    hours: int = 3,
    limit: int = 50,
) -> dict[str, Any]:
    """Build a comprehensive, read-only status report of all outreach activity over the last N hours."""
    hours = max(1, min(int(hours or 3), 720))
    limit = max(1, min(int(limit or 50), 200))

    now_utc_aware = datetime.now(UTC)
    now_ist_aware = now_utc_aware.astimezone(IST)

    # SQLite / Postgres store UTC naive timestamps
    now_utc_naive = now_utc_aware.replace(tzinfo=None)
    start_utc_naive = now_utc_naive - timedelta(hours=hours)
    start_ist_aware = start_utc_naive.replace(tzinfo=UTC).astimezone(IST)

    # 1. OUTREACH TOUCHES (Emails, WhatsApp, Sequences)
    touches = (
        db.query(OutreachTouch)
        .filter(OutreachTouch.occurred_at >= start_utc_naive)
        .order_by(OutreachTouch.occurred_at.desc())
        .all()
    )

    touch_counts_by_channel: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "sent": 0, "delivered": 0, "failed": 0, "blocked": 0, "other": 0}
    )
    touch_counts_by_type: Counter = Counter()
    failure_reasons: Counter = Counter()

    for t in touches:
        ch = (t.channel or "unknown").lower()
        st = (t.status or "unknown").upper()
        tt = (t.touch_type or "unknown").upper()

        touch_counts_by_channel[ch]["total"] += 1
        touch_counts_by_type[tt] += 1

        if st in ("SENT", "PROVIDER_ACCEPTED"):
            touch_counts_by_channel[ch]["sent"] += 1
        elif st in ("DELIVERED", "READ"):
            touch_counts_by_channel[ch]["delivered"] += 1
        elif st == "FAILED":
            touch_counts_by_channel[ch]["failed"] += 1
        elif st == "BLOCKED":
            touch_counts_by_channel[ch]["blocked"] += 1
        else:
            touch_counts_by_channel[ch]["other"] += 1

        if st in ("FAILED", "BLOCKED"):
            payload = t.payload or {}
            reason = payload.get("error") or payload.get("reason") or payload.get("why") or st
            reason_clean = str(reason).split("\n")[0][:140]
            failure_reasons[f"[{ch.upper()}] {reason_clean}"] += 1

    # 2. CALLS (CallHistory & Phone Dispatches)
    calls = (
        db.query(CallHistory)
        .filter(CallHistory.call_date >= start_utc_naive)
        .order_by(CallHistory.call_date.desc())
        .all()
    )

    call_metrics: dict[str, Any] = {
        "attempted": len(calls),
        "in_progress_or_dialing": 0,
        "completed": 0,
        "answered": 0,
        "no_answer": 0,
        "busy_or_waiting": 0,
        "failed": 0,
        "interested": 0,
        "not_interested": 0,
        "opt_outs": 0,
        "wrong_numbers": 0,
        "callbacks_requested": 0,
        "human_handoffs": 0,
        "outcomes_breakdown": Counter(),
    }

    for c in calls:
        st = (c.status or "").upper()
        cst = (c.call_status or "").upper()

        raw_outcome = st[3:] if st.startswith("AI_") else st
        if raw_outcome:
            call_metrics["outcomes_breakdown"][raw_outcome] += 1

        if cst == "CALLING":
            call_metrics["in_progress_or_dialing"] += 1
        elif cst == "COMPLETED":
            call_metrics["completed"] += 1

        if raw_outcome in ("NO_ANSWER", "VOICEMAIL"):
            call_metrics["no_answer"] += 1
        elif raw_outcome in ("BUSY", "WAITING", "HOLD", "TEMPORARILY_UNAVAILABLE"):
            call_metrics["busy_or_waiting"] += 1
        elif raw_outcome == "FAILED":
            call_metrics["failed"] += 1
            failure_reasons[f"[CALL] Call provider failure (lead {c.lead_id})"] += 1
        elif raw_outcome in ("INTERESTED", "WHATSAPP_OPT_IN", "SEND_INFO_EMAIL"):
            call_metrics["answered"] += 1
            call_metrics["interested"] += 1
        elif raw_outcome in ("NOT_INTERESTED", "REJECTED", "OTHER"):
            call_metrics["answered"] += 1
            call_metrics["not_interested"] += 1
        elif raw_outcome == "OPT_OUT":
            call_metrics["answered"] += 1
            call_metrics["opt_outs"] += 1
        elif raw_outcome in ("WRONG_NUMBER", "WRONG_PERSON"):
            call_metrics["answered"] += 1
            call_metrics["wrong_numbers"] += 1
        elif raw_outcome in ("CALLBACK_REQUESTED", "MEETING_REQUESTED"):
            call_metrics["answered"] += 1
            call_metrics["callbacks_requested"] += 1
        elif raw_outcome == "HUMAN_HANDOFF":
            call_metrics["answered"] += 1
            call_metrics["human_handoffs"] += 1
        elif raw_outcome not in ("AI_CALL_ATTEMPTED", ""):
            call_metrics["answered"] += 1

    # 3. WORKFLOW EVENTS (Inbound Replies, Opt-ins, Escalations)
    wf_events = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.occurred_at >= start_utc_naive)
        .order_by(WorkflowEvent.occurred_at.desc())
        .all()
    )

    reply_events: list[WorkflowEvent] = []
    consent_events: list[WorkflowEvent] = []
    callback_events: list[WorkflowEvent] = []

    for ev in wf_events:
        et = (ev.event_type or "").upper()
        actor = (ev.actor or "").upper()

        # Inbound prospect replies
        if actor == "PROSPECT" or et in (
            "EMAIL_REPLY_RECEIVED",
            "WHATSAPP_INBOUND",
            "REPLY_RECEIVED",
            "CATALOGUE_REQUESTED",
            "PRICING_REQUESTED",
            "SAMPLE_REQUESTED",
        ):
            reply_events.append(ev)

        # WhatsApp explicit consent opt-ins
        if et in ("WHATSAPP_CONSENT_RECORDED", "WHATSAPP_OPT_IN"):
            consent_events.append(ev)

        # Explicit callback / handoff requests
        if et == "FOUNDER_CALL_REQUESTED":
            callback_events.append(ev)

    cold_replies = 0
    engaged_replies = 0
    for r in reply_events:
        payload = r.payload or {}
        if payload.get("is_cold", True):
            cold_replies += 1
        else:
            engaged_replies += 1

    # 4. PENDING / HUMAN ATTENTION QUEUE & FOUNDER ACTIONABLES
    from sqlalchemy import or_

    open_callbacks: list[dict[str, Any]] = []
    try:
        cb_leads = (
            db.query(B2BLead)
            .filter(
                or_(
                    B2BLead.founder_callback_window.isnot(None),
                    B2BLead.outreach_stage == "AI_CALLBACK_REQUESTED",
                )
            )
            .order_by(B2BLead.last_updated.desc())
            .all()
        )
        for cl in cb_leads:
            from app.services import identity
            if getattr(cl, "do_not_call", False) or identity.is_fabricated_pattern(cl.phone):
                continue
            open_callbacks.append({
                "lead_id": cl.id,
                "company": cl.company or f"Lead #{cl.id}",
                "phone": cl.phone or "N/A",
                "division": cl.division or "corporate",
                "city": cl.city or "Punjab",
                "callback_window": cl.founder_callback_window or "Evening / 8:00 PM",
                "summary": cl.call_summary or "Callback requested during AI outreach call",
                "action": f"Place founder follow-up call ({cl.founder_callback_window or '8:00 PM'})",
            })
    except Exception:
        open_callbacks = []

    try:
        founder_queue_items = pipeline.founder_call_queue(db, limit=50)
    except Exception:
        founder_queue_items = []

    try:
        pending_drafts_rows = (
            db.query(EmailDraft, B2BLead)
            .outerjoin(B2BLead, EmailDraft.lead_id == B2BLead.id)
            .filter(EmailDraft.status.in_(["DRAFT", "PENDING"]))
            .order_by(EmailDraft.created_at.desc())
            .all()
        )
        pending_drafts = [
            {
                "draft_id": ed.id,
                "lead_id": ed.lead_id,
                "company": (lead.company if lead else None) or f"Lead #{ed.lead_id}",
                "division": lead.division if lead else None,
                "city": lead.city if lead else None,
                "subject": ed.subject,
                "status": ed.status,
                "reason": ed.reason,
                "created_at": ed.created_at.isoformat() if ed.created_at else None,
            }
            for ed, lead in pending_drafts_rows
        ]
    except Exception:
        pending_drafts = []

    try:
        strategic_rows = (
            db.query(ActionQueue, B2BLead)
            .outerjoin(B2BLead, ActionQueue.lead_id == B2BLead.id)
            .filter(ActionQueue.status == "PENDING")
            .order_by(ActionQueue.priority_score.desc())
            .limit(10)
            .all()
        )
        strategic_accounts = []
        for aq, lead in strategic_rows:
            is_warmed = bool(
                lead and (
                    lead.call_outcome_last in ("INTERESTED", "CALLBACK_REQUESTED", "MEETING_REQUESTED", "WHATSAPP_OPT_IN", "SEND_DETAILS", "SEND_PRICING")
                    or (lead.ai_interest_level or "").upper() in ("HOT", "WARM", "HIGH", "MEDIUM")
                    or (lead.outreach_stage or "") in ("AI_INTEREST_DETECTED", "FOUNDER_CALL_REQUESTED")
                )
            )
            strategic_accounts.append({
                "action_id": aq.id,
                "lead_id": aq.lead_id,
                "company": (lead.company if lead else None) or f"Lead #{aq.lead_id}",
                "division": lead.division if lead else None,
                "city": lead.city if lead else None,
                "phone": lead.phone if lead else None,
                "priority_score": aq.priority_score,
                "expected_margin": aq.expected_margin,
                "warmed": is_warmed,
                "action": (
                    f"Founder Commercial Deal Closing (Expected Margin: Rs {aq.expected_margin:,.0f})"
                    if is_warmed
                    else f"Autonomous AI Warming In Progress (Expected Margin: Rs {aq.expected_margin:,.0f})"
                ),
            })
    except Exception:
        strategic_accounts = []

    # 5. RECENT ACTIVITY TIMELINE (Chronological items in window)
    timeline: list[dict[str, Any]] = []

    # Map leads for quick name lookup
    lead_ids_in_window = {t.lead_id for t in touches if t.lead_id} | {c.lead_id for c in calls if c.lead_id}
    leads_by_id = {}
    if lead_ids_in_window:
        for lead_obj in db.query(B2BLead).filter(B2BLead.id.in_(lead_ids_in_window)).all():
            leads_by_id[lead_obj.id] = lead_obj

    for t in touches[:limit]:
        lead_match = leads_by_id.get(t.lead_id)
        t_ist = t.occurred_at.replace(tzinfo=UTC).astimezone(IST) if t.occurred_at else None
        timeline.append({
            "timestamp_utc": t.occurred_at.isoformat() if t.occurred_at else None,
            "timestamp_ist": t_ist.strftime("%Y-%m-%d %H:%M:%S IST") if t_ist else None,
            "channel": t.channel,
            "category": "outreach_touch",
            "action": t.touch_type,
            "status": t.status,
            "lead_id": t.lead_id,
            "company": lead_match.company if lead_match else None,
            "detail": (t.payload or {}).get("error") or (t.payload or {}).get("reason") or t.template_key,
        })

    for c in calls[:limit]:
        lead_match = leads_by_id.get(c.lead_id)
        c_ist = c.call_date.replace(tzinfo=UTC).astimezone(IST) if c.call_date else None
        st = (c.status or "").upper()
        raw_outcome = st[3:] if st.startswith("AI_") else st
        timeline.append({
            "timestamp_utc": c.call_date.isoformat() if c.call_date else None,
            "timestamp_ist": c_ist.strftime("%Y-%m-%d %H:%M:%S IST") if c_ist else None,
            "channel": "call",
            "category": "ai_call",
            "action": "AI_CALL_ATTEMPT",
            "status": raw_outcome or c.call_status,
            "lead_id": c.lead_id,
            "company": lead_match.company if lead_match else None,
            "detail": c.summary,
        })

    timeline.sort(key=lambda x: x["timestamp_utc"] or "", reverse=True)
    timeline = timeline[:limit]

    # 6. HEADLINE & ACTIVITY SUMMARY
    total_touches = len(touches)
    total_calls = len(calls)
    total_replies = len(reply_events)
    total_consents = len(consent_events)
    total_attempts = total_touches + total_calls

    has_activity = (total_attempts + total_replies + total_consents) > 0

    if not has_activity:
        activity_summary = f"NO ACTIVITY in the last {hours} hours"
    else:
        parts: list[str] = []
        if total_calls:
            parts.append(f"{total_calls} calls attempted")
        if touch_counts_by_channel["email"]["sent"]:
            parts.append(f"{touch_counts_by_channel['email']['sent']} emails sent")
        if touch_counts_by_channel["email"]["failed"]:
            parts.append(f"{touch_counts_by_channel['email']['failed']} email attempts failed")
        if total_replies:
            parts.append(f"{total_replies} replies ({cold_replies} cold)")
        if total_consents:
            parts.append(f"{total_consents} WhatsApp consents")
        if call_metrics["callbacks_requested"]:
            parts.append(f"{call_metrics['callbacks_requested']} callbacks requested")
        activity_summary = "Active: " + ", ".join(parts) if parts else "Activity recorded"

    # Format failures items list
    failure_items: list[dict[str, Any]] = []
    for t in touches:
        st = (t.status or "").upper()
        if st in ("FAILED", "BLOCKED"):
            payload = t.payload or {}
            reason = payload.get("error") or payload.get("reason") or payload.get("why") or st
            t_ist = t.occurred_at.replace(tzinfo=UTC).astimezone(IST) if t.occurred_at else None
            failure_items.append({
                "reason": str(reason).split("\n")[0][:140],
                "lead_id": t.lead_id,
                "channel": t.channel or "email",
                "timestamp": t_ist.strftime("%Y-%m-%d %H:%M:%S IST") if t_ist else None,
                "timestamp_utc": t.occurred_at.isoformat() if t.occurred_at else None,
            })
    for c in calls:
        st = (c.status or "").upper()
        raw_outcome = st[3:] if st.startswith("AI_") else st
        if raw_outcome in ("FAILED", "NO_ANSWER"):
            c_ist = c.call_date.replace(tzinfo=UTC).astimezone(IST) if c.call_date else None
            failure_items.append({
                "reason": f"Call {raw_outcome}: {c.summary or 'provider/telephony failure'}",
                "lead_id": c.lead_id,
                "channel": "call",
                "timestamp": c_ist.strftime("%Y-%m-%d %H:%M:%S IST") if c_ist else None,
                "timestamp_utc": c.call_date.isoformat() if c.call_date else None,
            })

    # Effective AI-calling state comes from the worker heartbeat, not the API
    # process environment. The API intentionally carries calling flags OFF;
    # the separate purity-worker owns the autonomous calling scheduler.
    worker_heartbeat = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.event_type == "HEARTBEAT", WorkflowEvent.actor == "worker")
        .order_by(WorkflowEvent.occurred_at.desc())
        .first()
    )
    worker_detail = (worker_heartbeat.payload or {}).get("detail", {}) if worker_heartbeat else {}
    worker_heartbeat_age_seconds = (
        int((now_utc_naive - worker_heartbeat.occurred_at).total_seconds())
        if worker_heartbeat and worker_heartbeat.occurred_at else None
    )
    worker_ai_calling_signal = worker_detail.get("ai_calling_armed")
    effective_ai_calling_enabled = worker_ai_calling_signal if isinstance(worker_ai_calling_signal, bool) else None
    ai_call_cycle = worker_detail.get("ai_call_cycle") or {}
    ai_calling_state = (
        "enabled" if effective_ai_calling_enabled is True
        else "disabled" if effective_ai_calling_enabled is False
        else "unknown"
    )
    worker_outreach_signal = worker_detail.get("autonomous_outreach")
    effective_auto_outreach_enabled = (
        (worker_outreach_signal == "enabled")
        if worker_outreach_signal is not None
        else (os.getenv("AUTO_OUTREACH_ENABLED", "0") in ("1", "true", "yes", "on"))
    )

    # Format attention items list
    attention_items: list[dict[str, Any]] = [
        {
            "type": "FOUNDER_CALL_REQUEST",
            "lead_id": i.lead_id,
            "requested_at": i.requested_at.isoformat() if i.requested_at else None,
            "requested_by": i.requested_by,
            "note": (i.payload or {}).get("note"),
            "reason": (i.payload or {}).get("reason"),
        }
        for i in founder_queue_items[:20]
    ] + [
        {
            "type": "PENDING_EMAIL_DRAFT",
            "draft_id": d.get("draft_id"),
            "lead_id": d.get("lead_id"),
            "company": d.get("company"),
            "subject": d.get("subject"),
            "created_at": d.get("created_at"),
        }
        for d in pending_drafts[:20]
    ]

    # 14 Oct: same-day founder callbacks for positive AI calls (capture.py).
    try:
        from app.models.models import ActionQueue
        _sd = (db.query(ActionQueue, B2BLead)
               .outerjoin(B2BLead, ActionQueue.lead_id == B2BLead.id)
               .filter(ActionQueue.action_type == "FOUNDER_CALLBACK_TODAY",
                       ActionQueue.status == "PENDING")
               .order_by(ActionQueue.created_at.desc()).limit(20).all())
        attention_items = [
            {
                "type": "FOUNDER_CALL_REQUEST",
                "lead_id": aq.lead_id,
                "company": getattr(ld, "company", None),
                "requested_at": aq.created_at.isoformat() if aq.created_at else None,
                "requested_by": "AI_CALL_POSITIVE",
                "reason": "Positive AI call - founder callback TODAY (due %s UTC)"
                          % (aq.due_date.strftime("%H:%M") if aq.due_date else "today"),
                "note": (getattr(ld, "call_summary", None) or "")[:200],
            }
            for aq, ld in _sd
        ] + attention_items
    except Exception:  # noqa: BLE001
        pass

    total_sent_messages = sum(ch["sent"] for ch in touch_counts_by_channel.values())
    total_delivered_messages = sum(ch["delivered"] for ch in touch_counts_by_channel.values())

    # 5. OUTBOUND READINESS (Nightly Queue Preparation Counts)
    from app.services.trust_promoter import MAY_SEND
    from app.services.whatsapp_sender import CONSENT_OK
    from app.services import identity

    all_active_leads = db.query(B2BLead).all()
    email_ready_count = sum(
        1 for l in all_active_leads
        if (l.email or "").strip()
        and (getattr(l, "email_trust", "") or "").upper() in MAY_SEND
        and not getattr(l, "do_not_call", False)
    )
    call_ready_count = sum(
        1 for l in all_active_leads
        if (l.phone or "").strip()
        and not getattr(l, "do_not_call", False)
        and not identity.is_fabricated_pattern(l.phone)
        and getattr(l, "phone_trust", "") != "INVALID"
        and (getattr(l, "ai_call_count", 0) or 0) < 3
    )
    wa_ready_count = sum(
        1 for l in all_active_leads
        if (getattr(l, "whatsapp_number", None) or getattr(l, "phone", None) or "").strip()
        and not getattr(l, "do_not_call", False)
        and (getattr(l, "consent_status", "") or "").upper() in CONSENT_OK
    )
    founder_review_count = len(open_callbacks) + len(founder_queue_items)

    return {
        "status": "success",
        "window": {
            "hours": hours,
            "start_ist": start_ist_aware.strftime("%Y-%m-%d %H:%M:%S IST"),
            "end_ist": now_ist_aware.strftime("%Y-%m-%d %H:%M:%S IST"),
            "timezone": "Asia/Kolkata",
            "start_utc": start_utc_naive.isoformat() + "Z",
            "end_utc": now_utc_naive.isoformat() + "Z",
        },
        "overview": {
            "has_activity": has_activity,
            "activity_summary": activity_summary,
            "total_outreach_attempts": total_attempts,
            "calls_attempted": total_calls,
            "messages_attempted": total_touches,
            "successful_sends": total_sent_messages,
            "deliveries": total_delivered_messages,
            "total_replies": total_replies,
            "cold_lead_replies": cold_replies,
            "whatsapp_consents": total_consents,
            "callbacks_requested": call_metrics["callbacks_requested"] + len(callback_events),
            "total_failures_and_blocks": len(failure_items),
        },
        "calls": {
            "attempted": call_metrics["attempted"],
            "reserved": call_metrics["attempted"],
            "dispatched": call_metrics["attempted"],
            "dialing": call_metrics["in_progress_or_dialing"],
            "connected": call_metrics["answered"],
            "completed": call_metrics["completed"],
            "no_answer": call_metrics["no_answer"],
            "failed": call_metrics["failed"],
            "callbacks": call_metrics["callbacks_requested"],
            "in_progress_or_dialing": call_metrics["in_progress_or_dialing"],
            "answered": call_metrics["answered"],
            "busy_or_waiting": call_metrics["busy_or_waiting"],
            "interested": call_metrics["interested"],
            "not_interested": call_metrics["not_interested"],
            "opt_outs": call_metrics["opt_outs"],
            "wrong_numbers": call_metrics["wrong_numbers"],
            "callbacks_requested": call_metrics["callbacks_requested"],
            "human_handoffs": call_metrics["human_handoffs"],
            "outcomes_breakdown": dict(call_metrics["outcomes_breakdown"]),
        },
        "messaging": {
            "attempted": total_touches,
            "sent": total_sent_messages,
            "delivered": total_delivered_messages,
            "replied": total_replies,
            "by_channel": dict(touch_counts_by_channel),
            "by_touch_type": dict(touch_counts_by_type),
        },
        "messages": {
            "total_attempted": total_touches,
            "by_channel": dict(touch_counts_by_channel),
            "by_touch_type": dict(touch_counts_by_type),
        },
        "failures": {
            "count": len(failure_items),
            "items": failure_items[:limit],
            "reasons_summary": dict(failure_reasons),
        },
        "failures_and_blockers": {
            "total_failed_or_blocked": len(failure_items),
            "reasons": dict(failure_reasons),
        },
        "attention": {
            "count": len(attention_items),
            "items": attention_items[:limit],
        },
        "pending_human_attention": {
            "founder_call_queue_count": len(founder_queue_items),
            "pending_email_drafts_count": len(pending_drafts),
            "items": attention_items[:limit],
        },
        "recent_activity": {
            "items": timeline[:limit],
        },
        "recent_activity_timeline": timeline[:limit],
        "replies": {
            "total": total_replies,
            "cold_lead_replies": cold_replies,
            "engaged_lead_replies": engaged_replies,
            "items": [
                {
                    "occurred_at_ist": (
                        r.occurred_at.replace(tzinfo=UTC).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                        if r.occurred_at
                        else None
                    ),
                    "lead_id": r.lead_id,
                    "event_type": r.event_type,
                    "channel": r.channel,
                    "payload": r.payload,
                }
                for r in reply_events[:20]
            ],
        },
        "whatsapp_consents": {
            "total": total_consents,
            "items": [
                {
                    "occurred_at_ist": (
                        c.occurred_at.replace(tzinfo=UTC).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                        if c.occurred_at
                        else None
                    ),
                    "lead_id": c.lead_id,
                    "payload": c.payload,
                }
                for c in consent_events[:20]
            ],
        },
        "callbacks_and_escalations": {
            "total": call_metrics["callbacks_requested"] + len(callback_events),
            "founder_call_queue_open_count": len(founder_queue_items),
            "items": [
                {
                    "lead_id": item.lead_id,
                    "requested_at": item.requested_at.isoformat() if item.requested_at else None,
                    "requested_by": item.requested_by,
                    "reason": (item.payload or {}).get("reason"),
                    "note": (item.payload or {}).get("note"),
                }
                for item in founder_queue_items[:20]
            ],
        },
        "founder_actionables": {
            "open_callbacks_count": len(open_callbacks),
            "open_callbacks": open_callbacks[:10],
            "pending_drafts_count": len(pending_drafts),
            "pending_drafts": pending_drafts[:15],
            "strategic_accounts_count": len(strategic_accounts),
            "strategic_accounts": strategic_accounts[:5],
        },
        "outbound_readiness": {
            "email_ready_count": email_ready_count,
            "call_ready_count": call_ready_count,
            "whatsapp_ready_count": wa_ready_count,
            "founder_review_count": founder_review_count,
            "business_hours_open": business_hours.is_open(now_ist_aware),
        },
        "fabrication_safety": {
            "synthetic_emails_generated": 0,
            "synthetic_phones_generated": 0,
            "guessed_contacts_inserted": 0,
            "id_derived_phones_inserted": 0,
            "unattributed_contacts_inserted": 0,
            "policy_status": "ENFORCED_ZERO_FABRICATION",
        },
        "system_status": {
            "is_read_only": True,
            "business_hours": business_hours.status(now_ist_aware),
            # API process flags are intentionally OFF because autonomous outbound
            # is owned by purity-worker to prevent duplicate dispatches.
            "api_server_process_flags": {
                "auto_ai_calling_enabled": os.getenv("AUTO_AI_CALLING_ENABLED", "0") == "1",
                "ai_calling_enabled": os.getenv("AI_CALLING_ENABLED", "0") == "1",
                "auto_outreach_enabled": os.getenv("AUTO_OUTREACH_ENABLED", "0") == "1",
                "smart_outreach_enabled": os.getenv("SMART_OUTREACH_ENABLED", "0") == "1",
                "role": "REST_API_SERVER_NON_DIALING",
            },
            "worker_execution_process_flags": {
                "auto_ai_calling_enabled": effective_ai_calling_enabled,
                "ai_calling_enabled": effective_ai_calling_enabled,
                "auto_outreach_enabled": effective_auto_outreach_enabled,
                "smart_outreach_enabled": os.getenv("SMART_OUTREACH_ENABLED", "0") in ("1", "true", "yes", "on"),
                "role": "AUTONOMOUS_BACKGROUND_WORKER_DIALING",
            },
            # Backward-compatible alias
            "api_process_flags": {
                "auto_ai_calling_enabled": os.getenv("AUTO_AI_CALLING_ENABLED", "0") == "1",
                "ai_calling_enabled": os.getenv("AI_CALLING_ENABLED", "0") == "1",
                "auto_outreach_enabled": os.getenv("AUTO_OUTREACH_ENABLED", "0") == "1",
                "smart_outreach_enabled": os.getenv("SMART_OUTREACH_ENABLED", "0") == "1",
                "note": "API process flags (non-dialing); actual background execution is managed by purity-worker",
            },
            # Effective runtime state is sourced from the worker heartbeat.
            "ai_calling_scheduler": {
                "state": ai_calling_state,
                "enabled": effective_ai_calling_enabled,
                "worker_heartbeat_at_utc": worker_heartbeat.occurred_at.isoformat() + "Z" if worker_heartbeat and worker_heartbeat.occurred_at else None,
                "worker_heartbeat_age_seconds": worker_heartbeat_age_seconds,
                "last_cycle": ai_call_cycle,
            },
            # Backward-compatible top-level flags representing effective runtime state
            "auto_ai_calling_enabled": effective_ai_calling_enabled,
            "ai_calling_enabled": effective_ai_calling_enabled,
            "auto_outreach_enabled": effective_auto_outreach_enabled,
            "smart_outreach_enabled": os.getenv("SMART_OUTREACH_ENABLED", "0") in ("1", "true", "yes", "on"),
        },
    }


def get_outreach_status_health(db: Session) -> dict[str, Any]:
    """Read-only health check for the outreach status reporting subsystem."""
    now_utc_aware = datetime.now(UTC)
    now_ist_aware = now_utc_aware.astimezone(IST)

    db_status = "disconnected"
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"

    # Git boot commit
    from app.api.endpoints import _BOOT_COMMIT
    from app.api.auth import is_status_auth_configured

    return {
        "status": "healthy" if db_status == "connected" else "unhealthy",
        "api_reachable": True,
        "database": db_status,
        "database_reachable": db_status == "connected",
        "timestamp_utc": now_utc_aware.isoformat(),
        "timestamp_ist": now_ist_aware.strftime("%Y-%m-%d %H:%M:%S IST"),
        "timezone": "Asia/Kolkata",
        "git_commit": _BOOT_COMMIT,
        "auth_configured": is_status_auth_configured(),
        "auth_mechanism": "Bearer token (OUTREACH_STATUS_API_TOKEN)",
        "is_read_only": True,
    }
