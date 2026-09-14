"""Startup checks for schemas that cannot be migrated automatically."""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.schema import MetaData


def assert_schema_compatible(engine: Engine, metadata: MetaData) -> None:
    """Fail closed when a live schema is missing model tables or columns."""
    inspector = inspect(engine)
    missing: list[str] = []

    for table_name, table in metadata.tables.items():
        if table_name not in inspector.get_table_names():
            missing.append(f"{table_name} (table)")
            continue

        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column in table.columns:
            if column.name not in existing_columns:
                missing.append(f"{table_name}.{column.name}")

    if missing:
        details = ", ".join(sorted(missing))
        raise RuntimeError(
            "database schema is incompatible with the loaded models; "
            f"missing: {details}. Apply the repository's schema update before startup."
        )
