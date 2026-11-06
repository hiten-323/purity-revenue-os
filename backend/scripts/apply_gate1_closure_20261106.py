#!/usr/bin/env python3
"""Apply Gate 1 terminal holds. Sends nothing and deletes nothing."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.services.gate1_holds import (
    CLOSURE_REASON, PERMANENTLY_UNMATCHED, REMINDER_REJECTED_STATUS,
    block_queue_without_provider_event, close_unrecoverable_fields,
    contact_status_correction, unmatched_reason_allowed,
)

DB = Path(__file__).resolve().parents[1] / "purity_beans.db"
ENV = Path(__file__).resolve().parents[1] / ".env"
FLAGS = (
    "AUTO_WARM_ENABLED", "SMART_OUTREACH_ENABLED", "AUTO_OUTREACH_ENABLED",
    "AI_CALLING_ENABLED", "AUTO_AI_CALLING_ENABLED", "AISENSY_ENABLED",
    "EMAIL_SENDING_ENABLED", "KLAVIYO_WA_ENABLED", "KLAVIYO_WA_FLOW_LIVE",
    "WHATSAPP_PACER_ENABLED",
)


def _flags():
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        vals[key.strip()] = value.strip().strip('"').strip("'")
    bad = [key for key in FLAGS if vals.get(key, "").lower() not in {"0", "false", "no", "off"}]
    if bad:
        raise SystemExit("refusing, outbound flags on: " + ",".join(bad))


def _event(con, lead_id, event_type, actor, now, before, after, payload):
    con.execute(
        """INSERT INTO workflow_events
           (event_type, lead_id, actor, channel, before_status, after_status, payload, occurred_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (event_type, lead_id, actor, "system", before, after, json.dumps(payload), now),
    )


def apply(con: sqlite3.Connection) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lead = con.execute(
        """SELECT id, company, phone, email, whatsapp_number, consent_phone, do_not_call,
                  dnc_reason, consent_status, contact_status, status
           FROM b2b_leads WHERE id=2305"""
    ).fetchone()
    if lead is None:
        raise RuntimeError("lead 2305 is missing")
    closed = close_unrecoverable_fields(dict(lead), now)
    con.execute(
        """UPDATE b2b_leads
           SET status=?, do_not_call=1, consent_status='UNKNOWN'
           WHERE id=2305""",
        (closed["status"],),
    )
    _event(con, 2305, closed["event"]["event_type"], closed["event"]["actor"], now,
           closed["event"]["before_status"], closed["event"]["after_status"], closed["event"]["payload"])
    reminder = con.execute(
        "SELECT id, status, reason FROM outreach_reminders WHERE lead_id=2305 AND status='SCHEDULED'"
    ).fetchone()
    if reminder:
        con.execute(
            "UPDATE outreach_reminders SET status='CANCELLED', done_at=? WHERE id=?",
            (now, reminder["id"]),
        )

    row = con.execute(
        "SELECT id, lead_id, status, whatsapp_response, delivered_at FROM outbound_whatsapp WHERE id=16"
    ).fetchone()
    if row is None or row["lead_id"] != 1840 or row["status"] != "QUEUED_TO_KLAVIYO":
        raise RuntimeError("WhatsApp row 16 is not the queued lead 1840 row")
    blocked = block_queue_without_provider_event(row["status"], row["whatsapp_response"])
    con.execute(
        "UPDATE outbound_whatsapp SET status=?, whatsapp_response=? WHERE id=16",
        (blocked["status"], blocked["whatsapp_response"]),
    )

    open_rows = con.execute(
        """SELECT provider_event_id, reason FROM klaviyo_delivery_quarantine
           WHERE resolution_status='OPEN'"""
    ).fetchall()
    if len(open_rows) != 7 or any(not unmatched_reason_allowed(r["reason"]) for r in open_rows):
        raise RuntimeError("open quarantine set is not the seven unmatched events")
    con.execute(
        """UPDATE klaviyo_delivery_quarantine
           SET resolution_status=?
           WHERE resolution_status='OPEN'""",
        (PERMANENTLY_UNMATCHED,),
    )

    corrected = 0
    leads = con.execute(
        """SELECT id, do_not_call, consent_status, status, contact_status
           FROM b2b_leads WHERE UPPER(COALESCE(contact_status,''))='CONTACTABLE'"""
    ).fetchall()
    for item in leads:
        class L:
            pass
        holder = L()
        holder.do_not_call = item["do_not_call"]
        holder.consent_status = item["consent_status"]
        holder.status = item["status"]
        holder.contact_status = item["contact_status"]
        change = contact_status_correction(holder)
        if change is None:
            continue
        status, reason = change
        con.execute(
            "UPDATE b2b_leads SET contact_status=?, contact_status_reason=? WHERE id=? AND UPPER(COALESCE(contact_status,''))='CONTACTABLE'",
            (status, reason, item["id"]),
        )
        _event(con, item["id"], "CONTACT_STATUS_SET", "GATE1_REMEDIATION", now,
               "CONTACTABLE", status, {"status": status, "reason": reason})
        corrected += 1

    held = con.execute(
        """SELECT id, lead_id, channel, status FROM outreach_reminders
           WHERE lead_id IN (201,171,93) AND channel='whatsapp' AND status='SCHEDULED'"""
    ).fetchall()
    if sorted(r["lead_id"] for r in held) != [93, 171, 201]:
        raise RuntimeError("expected scheduled WhatsApp reminders for leads 201, 171, and 93")
    for item in held:
        con.execute(
            "UPDATE outreach_reminders SET status=?, done_at=? WHERE id=?",
            (REMINDER_REJECTED_STATUS, now, item["id"]),
        )
        _event(con, item["lead_id"], "REMINDER_REJECTED_NO_CONSENT", "GATE1_REMEDIATION", now,
               "SCHEDULED", REMINDER_REJECTED_STATUS,
               {"reminder_id": item["id"], "channel": "whatsapp",
                "reason": "consent_status=UNKNOWN"})

    # Post-conditions. Roll back the whole write if any of these fail.
    lead_after = con.execute(
        """SELECT status, do_not_call, consent_status, contact_status, phone, email,
                  whatsapp_number, company FROM b2b_leads WHERE id=2305"""
    ).fetchone()
    if lead_after["status"] != "CLOSED_UNRECOVERABLE" or lead_after["do_not_call"] != 1 \
            or (lead_after["consent_status"] or "").upper() != "UNKNOWN" \
            or lead_after["contact_status"] != "QUARANTINED" \
            or lead_after["phone"] or lead_after["email"] or lead_after["whatsapp_number"]:
        raise RuntimeError("lead 2305 closure did not hold")
    wa = con.execute("SELECT status, delivered_at FROM outbound_whatsapp WHERE id=16").fetchone()
    if wa["status"] != "BLOCKED_NO_PROVIDER_EVENT" or wa["delivered_at"] is not None:
        raise RuntimeError("row 16 was not terminally blocked")
    other = con.execute("SELECT status FROM outbound_whatsapp WHERE id=5").fetchone()
    if other["status"] != "BLOCKED_DNC":
        raise RuntimeError("lead 2239 WhatsApp row changed")
    lead_2239 = con.execute(
        "SELECT do_not_call, consent_status, status, contact_status FROM b2b_leads WHERE id=2239"
    ).fetchone()
    if lead_2239["do_not_call"] != 1 or lead_2239["consent_status"] != "REVOKED" \
            or lead_2239["status"] != "WRONG_NUMBER" or lead_2239["contact_status"] != "DO_NOT_CONTACT":
        raise RuntimeError("lead 2239 flags were not preserved")
    dnc_contactable = con.execute(
        """SELECT COUNT(*) FROM b2b_leads
           WHERE COALESCE(do_not_call,0)=1 AND UPPER(COALESCE(contact_status,''))='CONTACTABLE'"""
    ).fetchone()[0]
    if dnc_contactable:
        raise RuntimeError("DNC leads remain CONTACTABLE")
    open_left = con.execute(
        "SELECT COUNT(*) FROM klaviyo_delivery_quarantine WHERE resolution_status='OPEN'"
    ).fetchone()[0]
    permanent = con.execute(
        """SELECT COUNT(*) FROM klaviyo_delivery_quarantine
           WHERE resolution_status=?""",
        (PERMANENTLY_UNMATCHED,),
    ).fetchone()[0]
    if open_left or permanent != 7:
        raise RuntimeError("quarantine lock count is wrong")
    cleared = con.execute(
        "SELECT COUNT(*) FROM dnc_audit_events WHERE event_type='DNC_CLEARED'"
    ).fetchone()[0]
    if cleared:
        raise RuntimeError("a DNC clear was recorded")
    return {
        "closed_lead": 2305,
        "closed_status": lead_after["status"],
        "whatsapp_16": wa["status"],
        "contact_corrections": corrected,
        "permanent_unmatched": permanent,
        "reminders_rejected": [r["id"] for r in held],
        "at": now,
    }


def main() -> int:
    _flags()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN")
        result = apply(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    print("GATE1_CLOSURE", json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
