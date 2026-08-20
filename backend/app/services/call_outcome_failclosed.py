"""
Fail-closed patch for outreach_search.apply_call_outcome.

When decide_after_call cannot answer, the previous code fell back to the local
OUTCOMES registry (oc.next_action). That registry is presentation — labels and
guidance — not a second decision engine. Falling back reintroduced dual authority.

This module replaces apply_call_outcome so engine failure queues FOUNDER_REVIEW.
Loaded from main.py after models/services are importable.

Also installs the OSM discovery fallback (Nominatim + Overpass) when
DISCOVERY_MAPS_PROVIDER is osm/auto.
"""
from __future__ import annotations

from datetime import datetime


def install() -> None:
    from app.services import outreach_search as m

    def apply_call_outcome(db, lead, outcome_key: str, captured: dict | None = None) -> dict:
        from app.models.models import LeadInteraction, WorkflowEvent

        key = (outcome_key or "").upper().strip()
        if key not in m.OUTCOMES:
            raise ValueError(
                f"unknown outcome '{outcome_key}' — expected one of "
                f"{sorted(m.OUTCOMES)}"
            )
        oc = m.OUTCOMES[key]
        cap = captured or {}

        i = LeadInteraction(
            lead_id=lead.id, occurred_at=datetime.utcnow(), created_by="FOUNDER",
            method="founder_call", outcome=key,
            decision_maker=cap.get("decision_maker"),
            designation=cap.get("designation"),
            current_supplier=cap.get("current_supplier"),
            preferred_contact_time=cap.get("preferred_contact_time"),
            preferred_contact_method=cap.get("preferred_contact_method"),
            next_followup_date=cap.get("next_followup_date"),
            monthly_consumption_kg=cap.get("monthly_consumption_kg"),
            interested=True if key in (
                "INTERESTED", "DECISION_MAKER_FOUND",
                "SAMPLE_REQUESTED", "MEETING_REQUESTED",
            ) else (False if key in ("NOT_INTERESTED", "DO_NOT_CONTACT") else None),
            remark=cap.get("remark"),
        )
        db.add(i)

        promoted = []
        for f in ("decision_maker", "current_supplier", "next_followup_date"):
            if cap.get(f) and hasattr(lead, f):
                setattr(lead, f, cap[f])
                promoted.append(f)

        if cap.get("email"):
            lead.email = cap["email"].strip()
            lead.email_verification_status = "FOUNDER_CALL_PROVIDED"
            lead.email_verified = True
            promoted.append("email")
            db.add(WorkflowEvent(
                lead_id=lead.id, event_type="CONTACT_CAPTURED", actor="FOUNDER",
                channel="founder_call",
                payload={
                    "email": cap["email"], "source": "FOUNDER_CALL",
                    "note": "given by the prospect on a call — attributable, "
                            "so the next email is a follow-up not an intro",
                },
                occurred_at=datetime.utcnow(),
            ))

        if key == "DO_NOT_CONTACT":
            lead.do_not_call = True
            lead.status = "DO_NOT_CONTACT"
        elif key == "NOT_INTERESTED":
            lead.status = "CLOSED_LOST"
        elif key in ("INTERESTED", "DECISION_MAKER_FOUND"):
            lead.status = "QUALIFIED"
        elif key == "SAMPLE_REQUESTED":
            lead.status = "SAMPLE_REQUESTED"
        elif key == "MEETING_REQUESTED":
            lead.status = "MEETING_BOOKED"

        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="FOUNDER_CALL", actor="FOUNDER",
            channel="phone",
            payload={
                "outcome": key,
                "captured": {k: v for k, v in cap.items() if v},
                "promoted": promoted,
            },
            occurred_at=datetime.utcnow(),
        ))
        db.commit()

        try:
            from app.services.phone_intelligence import decide_after_call
            decided = decide_after_call(lead, db, key, cap, cap.get("remark") or "")
            action_type, delay = decided["action"], oc.delay_days
            if action_type in ("NONE", "WAIT"):
                action_type = None
            reason = f"call outcome {key} -> {decided['decided_by']}"
            if decided.get("blocked"):
                reason += f" (blocked: {decided['blocked']})"
        except Exception as e:
            action_type, delay = "FOUNDER_REVIEW", 0
            reason = (
                f"call outcome {key} — decision engine unavailable "
                f"({e.__class__.__name__}: {e}); FOUNDER_REVIEW, not registry"
            )

        nxt = m.set_next_action(db, lead, action_type, delay, reason=reason)
        missing = [f for f in oc.needs if not cap.get(f)]
        return {
            "outcome": key,
            "label": oc.label,
            "guidance": oc.note,
            "interaction_id": i.id,
            "promoted": promoted,
            "next_action": action_type,
            "channel": oc.channel,
            "due_in_days": oc.delay_days,
            "terminal": oc.terminal,
            "next_action_state": nxt,
            "not_captured": missing,
        }

    m.apply_call_outcome = apply_call_outcome

    try:
        from app.services.osm_places import install_osm_maps_fallback
        install_osm_maps_fallback()
    except Exception as e:
        print(f"[OSM] maps fallback not installed: {e}")
