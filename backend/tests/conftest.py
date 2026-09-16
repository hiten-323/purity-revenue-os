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

# Load backend/.env once, here, before any test runs or imports a
# dotenv-loading module of its own (email_sender.py, marketplace_director.py,
# nuraveda_provider.py, ...). Each of those modules also self-loads — see
# email_sender.py's docstring for the incident that pattern exists to
# prevent — but that self-load only fires on a module's FIRST import in this
# process, which python caches. If that first import happens to occur lazily
# inside a test, AFTER that test's own monkeypatch.delenv() of the same key,
# the self-load resurrects the real .env value and silently undoes the
# test's deletion for the rest of that test. Loading once here, before
# collection reaches any test body, means every module's later self-load is
# a harmless no-op (already loaded) instead of a mid-test surprise.
try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND / ".env")
except Exception as _e:
    print(f"[conftest] .env load skipped: {_e}")

# Force the one-time self-load in each dotenv-loading leaf module to happen
# HERE, at collection time, rather than whenever a test first happens to
# import one of them. Python caches imports, so a module's top-level code —
# including its own load_dotenv() call — runs at most once per process; if
# that first import falls lazily inside a test (as app.services.providers
# import chains do), it can land AFTER that same test's own
# monkeypatch.delenv() and silently resurrect the value the test just
# cleared. Importing them here means later imports (from tests or app code)
# are no-ops against an already-populated os.environ.
try:
    import app.services.email_sender  # noqa: F401,E402
    import app.services.marketplace_director  # noqa: F401,E402
    import app.services.nuraveda_provider  # noqa: F401,E402
    import app.services.preference_registry  # noqa: F401,E402
except Exception as _e:
    print(f"[conftest] eager dotenv-module import skipped: {_e}")


def memory_engine():
    """A private, schema-less in-memory SQLite engine that survives reconnects."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
