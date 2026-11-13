"""Unit tests for Auto-Outreach 3-Hour Status Report & Hourly Priority Alert Engine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import pytest

from app.database.database import Base
from app.models.models import B2BLead, CallHistory, EmailDraft, WorkflowEvent
from app.services.smart_outreach import OutreachTouch
from app.services.outreach_intelligence.reporting_jobs import (
    format_3hour_status_report,
    run_hourly_priority_alert,
    _make_event_key,
)
from conftest import memory_engine


@pytest.fixture
def db_session():
    engine = memory_engine()
    Base.metadata.create_all(engine, tables=[
        B2BLead.__table__, CallHistory.__table__, WorkflowEvent.__table__,
        EmailDraft.__table__, OutreachTouch.__table__,
    ])
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine)()


def test_format_3hour_report_no_activity():
    empty_status = {
        "status": "success",
        "window": {
            "hours": 3,
            "start_ist": "2026-09-27 13:00:00 IST",
            "end_ist": "2026-09-27 16:00:00 IST",
            "start_utc": "2026-09-27T07:30:00Z",
            "end_utc": "2026-09-27T10:30:00Z",
        },
        "overview": {
            "has_activity": False,
            "activity_summary": "NO ACTIVITY in the last 3 hours",
            "total_outreach_attempts": 0,
            "calls_attempted": 0,
            "messages_attempted": 0,
        },
        "system_status": {
            "is_read_only": True,
            "business_hours": {"open": True, "timezone": "Asia/Kolkata"},
            "auto_ai_calling_enabled": False,
            "ai_calling_scheduler": {"state": "disabled"},
        },
    }

    report = format_3hour_status_report(empty_status)
    assert "2026-09-27 13:00:00 IST → 2026-09-27 16:00:00 IST" in report
    assert "NO ACTIVITY IN THE LAST 3 HOURS" in report
    assert "Read-Only Mode           : VERIFIED (True)" in report


def test_format_3hour_report_with_activity():
    active_status = {
        "status": "success",
        "window": {
            "hours": 3,
            "start_ist": "2026-09-27 13:00:00 IST",
            "end_ist": "2026-09-27 16:00:00 IST",
        },
        "overview": {
            "has_activity": True,
            "activity_summary": "Active: 5 calls attempted, 2 emails sent",
            "total_outreach_attempts": 7,
            "calls_attempted": 5,
            "messages_attempted": 2,
            "successful_sends": 2,
            "deliveries": 1,
            "total_replies": 1,
            "cold_lead_replies": 1,
            "whatsapp_consents": 1,
            "callbacks_requested": 1,
            "total_failures_and_blocks": 2,
        },
        "calls": {
            "attempted": 5,
            "answered": 3,
            "completed": 3,
            "interested": 1,
            "no_answer": 2,
            "failed": 0,
            "opt_outs": 0,
            "wrong_numbers": 0,
            "human_handoffs": 0,
            "outcomes_breakdown": {"INTERESTED": 1, "NO_ANSWER": 2},
        },
        "messaging": {
            "attempted": 2,
            "sent": 2,
            "delivered": 1,
            "by_channel": {"email": {"sent": 2, "failed": 0}},
            "by_touch_type": {"WARM_FIRST_TOUCH": 2},
        },
        "failures": {
            "count": 1,
            "reasons_summary": {"[EMAIL] domain NXDOMAIN": 1},
        },
        "attention": {
            "items": [{"type": "FOUNDER_CALL_REQUEST", "lead_id": 42, "reason": "VIP Callback"}],
        },
        "system_status": {
            "is_read_only": True,
            "business_hours": {"open": True, "timezone": "Asia/Kolkata"},
            "auto_ai_calling_enabled": True,
            "ai_calling_scheduler": {"state": "enabled"},
        },
    }

    report = format_3hour_status_report(active_status)
    assert "2026-09-27 13:00:00 IST → 2026-09-27 16:00:00 IST" in report
    assert "Total Outreach Attempts : 7" in report
    assert "Calls Attempted          : 5" in report
    assert "Inbound Replies          : 1" in report
    assert "WhatsApp Consents/Opt-ins: 1" in report
    assert "[EMAIL] domain NXDOMAIN : 1" in report
    assert "Lead #42: VIP Callback" in report


def test_hourly_alert_triggers_on_priority_events(tmp_path):
    checkpoint_file = tmp_path / "test_checkpoint.json"

    mock_status_payload = {
        "status": "success",
        "window": {
            "hours": 1,
            "start_ist": "2026-09-27 15:00:00 IST",
            "end_ist": "2026-09-27 16:00:00 IST",
        },
        "replies": {
            "total": 1,
            "items": [{
                "lead_id": 101,
                "channel": "email",
                "event_type": "EMAIL_REPLY_RECEIVED",
                "occurred_at_ist": "2026-09-27 15:20:00 IST",
                "payload": {"text": "Please share the price list", "is_cold": True},
            }],
        },
        "whatsapp_consents": {
            "total": 1,
            "items": [{
                "lead_id": 102,
                "occurred_at_ist": "2026-09-27 15:25:00 IST",
                "payload": {"source": "AI_CALL_WHATSAPP_REQUEST"},
            }],
        },
        "callbacks_and_escalations": {
            "items": [{
                "lead_id": 103,
                "requested_at": "2026-09-27T15:30:00",
                "reason": "Owner requested call back at 4 PM",
            }],
        },
        "failures": {
            "count": 1,
            "items": [{
                "lead_id": 104,
                "channel": "email",
                "reason": "BLOCKED: invalid domain syntax",
                "timestamp_utc": "2026-09-27T09:40:00Z",
                "timestamp": "2026-09-27 15:40:00 IST",
            }],
        },
        "recent_activity_timeline": [],
        "attention": {"items": []},
    }

    with patch("app.services.outreach_intelligence.reporting_jobs.fetch_live_status", return_value=mock_status_payload):
        # First Run: New events present -> must generate alert
        has_new, alert_text, metrics = run_hourly_priority_alert(checkpoint_path=checkpoint_file, hours=1)
        assert has_new is True
        assert alert_text is not None
        assert "2026-09-27 15:00:00 IST → 2026-09-27 16:00:00 IST" in alert_text
        assert "NEW INBOUND REPLIES" in alert_text
        assert "Lead #101" in alert_text
        assert "Please share the price list" in alert_text
        assert "NEW WHATSAPP CONSENTS" in alert_text
        assert "Lead #102" in alert_text
        assert "NEW FOUNDER INVOLVEMENT REQUESTS" in alert_text
        assert "Lead #103" in alert_text
        assert "BLOCKED: invalid domain syntax" in alert_text

        # Second Run with same data: Must be strictly SILENT (has_new=False, alert_text=None)
        has_new_2, alert_text_2, metrics_2 = run_hourly_priority_alert(checkpoint_path=checkpoint_file, hours=1)
        assert has_new_2 is False
        assert alert_text_2 is None
        assert metrics_2["silent"] is True
        assert metrics_2["new_events_count"] == 0


def test_hourly_alert_silent_when_empty_window(tmp_path):
    checkpoint_file = tmp_path / "test_checkpoint_empty.json"

    empty_status = {
        "status": "success",
        "window": {"hours": 1, "start_ist": "2026-09-27 15:00:00 IST", "end_ist": "2026-09-27 16:00:00 IST"},
        "replies": {"items": []},
        "whatsapp_consents": {"items": []},
        "callbacks_and_escalations": {"items": []},
        "failures": {"items": []},
        "attention": {"items": []},
        "recent_activity_timeline": [],
    }

    with patch("app.services.outreach_intelligence.reporting_jobs.fetch_live_status", return_value=empty_status):
        has_new, alert_text, metrics = run_hourly_priority_alert(checkpoint_path=checkpoint_file, hours=1)
        assert has_new is False
        assert alert_text is None
        assert metrics["silent"] is True
