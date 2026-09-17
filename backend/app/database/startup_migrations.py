"""Small, idempotent migrations that must exist before the API serves traffic."""
from __future__ import annotations

from sqlalchemy import inspect, text


def run_startup_migrations() -> None:
    """Create the eight outreach columns missing from the legacy startup map."""
    from app.database.database import Base, engine
    import app.models.models  # noqa: F401 - register metadata before create_all

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "b2b_leads" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("b2b_leads")}
    missing = {
        "consent_phone": "VARCHAR",
        "whatsapp_verified": "BOOLEAN",
        "whatsapp_verified_at": "TIMESTAMP",
        "outreach_stage": "VARCHAR",
        "outreach_stage_at": "TIMESTAMP",
        "ai_call_count": "INTEGER",
        "ai_interest_level": "VARCHAR",
        "founder_callback_window": "VARCHAR",
    }

    with engine.begin() as conn:
        for column, column_type in missing.items():
            if column not in existing:
                conn.execute(text(
                    f"ALTER TABLE b2b_leads ADD COLUMN {column} {column_type}"
                ))
