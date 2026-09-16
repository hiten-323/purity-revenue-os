from datetime import datetime, timedelta

from sqlalchemy.orm import sessionmaker

from app.models.models import B2BLead, Base
from app.services import outreach_orchestrator as o
from conftest import memory_engine


def _lead(db):
    lead = B2BLead(
        company="Consent Order Test",
        segment="horeca",
        phone="9876543210",
        whatsapp_number="9876543210",
        phone_verified=True,
        whatsapp_verified=True,
        stage_entered_date=datetime.utcnow() - timedelta(days=30),
    )
    db.add(lead)
    db.commit()
    return lead


def test_whatsapp_gate_requires_ai_call_consent_provenance():
    """Explicit consent alone is insufficient for Smart Outreach WhatsApp.

    The required flow is a disclosed AI consent call followed by WhatsApp.
    """
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = _lead(db)
        lead.consent_status = "EXPLICIT"
        db.commit()

        allowed, reason = o._whatsapp_ok(lead, db)
        assert allowed is False
        assert "AI consent call" in reason

        lead.ai_call_count = 1
        lead.consent_source = "AI_CALL_WHATSAPP_REQUEST"
        db.commit()

        allowed, reason = o._whatsapp_ok(lead, db)
        assert allowed is True
    finally:
        db.close()
        engine.dispose()


def test_whatsapp_is_never_scheduled_before_ai_consent_call(monkeypatch):
    """The required Smart Outreach order is phone consent first, then WhatsApp.

    A verified WhatsApp number proves technical reachability only; it is not
    permission to initiate WhatsApp outreach. The AI call must obtain explicit
    WhatsApp consent first.
    """
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        lead = _lead(db)

        # Isolate sequencing from provider/DND configuration. Both channels
        # are technically eligible; ordering itself must still put the AI call
        # before the WhatsApp touch.
        monkeypatch.setitem(o.GATES, o.PHONE, lambda lead, db: (True, "AI call eligible"))
        monkeypatch.setitem(o.GATES, o.WHATSAPP, lambda lead, db: (True, "verified + consent eligible"))
        monkeypatch.setitem(o.GATES, o.EMAIL, lambda lead, db: (False, "email unavailable"))
        monkeypatch.setitem(o.GATES, o.LINKEDIN, lambda lead, db: (False, "manual only"))

        result = o.next_touch(lead, db)
        assert result["channel"] == o.PHONE
    finally:
        db.close()
        engine.dispose()
