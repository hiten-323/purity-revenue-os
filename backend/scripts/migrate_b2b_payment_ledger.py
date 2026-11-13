"""Idempotent schema migration for the founder-verified B2B payment ledger."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import inspect, text  # noqa: E402
from app.database.database import Base, engine  # noqa: E402
import app.models.models  # noqa: E402,F401 - register B2BPayment


def main() -> None:
    if engine.dialect.name == "sqlite":
        db_path = Path(engine.url.database)
        backup_path = Path("/root/purity-recovery-backup-20261109/purity_beans_pre_b2b_payment_ledger_20261109.db")
        if db_path.exists() and not backup_path.exists():
            src = sqlite3.connect(str(db_path), timeout=60)
            dst = sqlite3.connect(str(backup_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

    Base.metadata.create_all(bind=engine)
    columns = {column["name"] for column in inspect(engine).get_columns("lead_attribution_paths")}
    with engine.begin() as conn:
        if "payment_reference" not in columns:
            conn.execute(text("ALTER TABLE lead_attribution_paths ADD COLUMN payment_reference VARCHAR"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_lead_attribution_paths_payment_reference "
            "ON lead_attribution_paths (payment_reference)"
        ))
    print("B2B payment ledger schema is ready.")


if __name__ == "__main__":
    main()
