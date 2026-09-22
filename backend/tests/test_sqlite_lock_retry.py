"""Focused tests for SQLite lock retry helper."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.database.database import commit_with_retry, is_sqlite_locked


def test_is_sqlite_locked_detects_message():
    assert is_sqlite_locked(OperationalError("statement", {}, Exception("database is locked")))
    assert is_sqlite_locked(sqlite3.OperationalError("database is locked"))
    assert not is_sqlite_locked(OperationalError("statement", {}, Exception("no such table")))


def test_commit_with_retry_succeeds_after_locks():
    db = MagicMock()
    calls = {"n": 0}

    def _commit():
        calls["n"] += 1
        if calls["n"] < 3:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    db.commit.side_effect = _commit
    commit_with_retry(db, attempts=5, base_delay=0.001)
    assert calls["n"] == 3


def test_commit_with_retry_gives_up():
    db = MagicMock()
    db.commit.side_effect = OperationalError("COMMIT", {}, Exception("database is locked"))
    with pytest.raises(OperationalError):
        commit_with_retry(db, attempts=3, base_delay=0.001)
