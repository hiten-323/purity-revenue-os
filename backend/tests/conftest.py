"""
Shared test plumbing.

Why an in-memory engine
-----------------------
Every db fixture in this suite built a temp-file SQLite database and ran
Base.metadata.create_all() on it. There are 40 tables, and on Windows the
fsync cost of creating them dominates:

    file-backed create_all   665 ms per test
    in-memory create_all      25 ms per test

Multiplied across the suite that was most of the wall clock. Nothing in these
tests reads the database through its path -- only through the session -- so the
file bought nothing.

StaticPool is not optional here. A plain in-memory engine hands out a NEW empty
database per connection, so the schema created on one connection is invisible
to the next and the tests fail in a way that looks like a data bug rather than
a pooling one. StaticPool pins a single connection for the engine's lifetime.

Two fixtures deliberately still use a real file, and should stay that way:
test_whatsapp_connector_consent (the connector opens the DB by path) and
test_failclosed_gate (its listener reads through a second connection).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def memory_engine():
    """A private, schema-less in-memory SQLite engine that survives reconnects."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
