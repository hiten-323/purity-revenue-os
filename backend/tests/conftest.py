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
import os as _os_offpeak
_os_offpeak.environ.setdefault('AI_CALL_OFFPEAK_FRACTION', '1')  # 14 Oct: calling-window weighting is time-of-day dependent; neutral in tests

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 3 Nov 2026: tests must never read or write the LIVE email day counter, and
# the live .env's email switch/cap must not leak into tests. Set these BEFORE
# any .env load (load_dotenv never overrides an existing variable).
import os as _os_email
import tempfile as _tf_email

# CRITICAL: test processes must never bind SessionLocal to the production CRM.
# Several legacy tests instantiate SessionLocal directly (outside the db fixture),
# so isolating only fixture-managed sessions is insufficient. Give this pytest
# process its own file-backed SQLite database before any application imports or
# dotenv loading. File-backed storage preserves multi-connection webhook tests.
_PYTEST_DB = _os_offpeak.path.join(
    _tf_email.gettempdir(), f"purity_revenue_pytest_{_os_offpeak.getpid()}.db"
)
_os_offpeak.environ["DATABASE_URL"] = f"sqlite:///{_PYTEST_DB}"
_os_email.environ["EMAIL_DAILY_STATE_FILE"] = _os_email.path.join(
    _tf_email.mkdtemp(prefix="pytest_email_"), "email_daily_count.json")
_os_email.environ["EMAIL_SENDING_ENABLED"] = "1"
_os_email.environ["EMAIL_DAILY_CAP"] = "15"

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

import os
os.environ["OUTREACH_BUSINESS_HOURS"] = "0"
os.environ["AI_CALL_BUSINESS_HOURS"] = "0"
# Keep production's emergency-stop setting from masking the specific policy
# under test. Tests that exercise the emergency stop explicitly set it to 1.
os.environ["AI_CALLING_KILL_SWITCH"] = "0"

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

# Legacy tests using SessionLocal directly need the same isolated schema.
try:
    from app.database.database import Base as _TestBase, engine as _test_engine
    from app.models import models as _test_models  # noqa: F401
    _TestBase.metadata.create_all(bind=_test_engine)
except Exception as _e:
    raise RuntimeError(f"pytest isolated database bootstrap failed: {_e}") from _e


def memory_engine():
    """A private, schema-less in-memory SQLite engine that survives reconnects."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

os.environ['KLAVIYO_WA_SYNC_ENABLED'] = '0'  # 21 Oct: never push test leads to live Klaviyo


# 25 Oct 2026 human_pace: its throttle state is process-global; start each test fresh.
import pytest as _hp_pytest


@_hp_pytest.fixture(autouse=True)
def _reset_human_pace():
    try:
        from app.services import human_pace as _hp
        _hp._state.update(email_last=None, email_gap=0.0, email_hour=None,
                          email_hour_count=0, email_hour_cap=None, call_next_at=0.0)
    except Exception:
        pass
    yield


# 3 Nov 2026: every test gets its own empty email day counter.
@_hp_pytest.fixture(autouse=True)
def _isolated_email_counter(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_DAILY_STATE_FILE", str(tmp_path / "email_daily_count.json"))
    yield
