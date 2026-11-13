"""Focused tests for SQLite lock retry helper."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.database.database import (LockedWriteLost, commit_with_retry,
                                   is_sqlite_locked, run_with_retry)


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
    redone = {"n": 0}
    commit_with_retry(db, attempts=5, base_delay=0.001,
                      redo=lambda: redone.__setitem__("n", redone["n"] + 1))
    assert calls["n"] == 3
    # fix4: each retry re-applies the unit of work after the rollback
    assert redone["n"] == 2
    assert db.rollback.call_count == 2


def test_commit_with_retry_without_redo_raises_instead_of_empty_commit():
    db = MagicMock()
    n = {"c": 0}

    def _commit():
        n["c"] += 1
        if n["c"] == 1:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    db.commit.side_effect = _commit
    with pytest.raises(LockedWriteLost):
        commit_with_retry(db, attempts=5, base_delay=0.001)
    assert n["c"] == 1  # never "commits" the rolled-back, empty session


def test_run_with_retry_reapplies_writes():
    db = MagicMock()
    n = {"c": 0, "fn": 0}

    def _commit():
        n["c"] += 1
        if n["c"] == 1:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    def fn():
        n["fn"] += 1
        return "done"

    db.commit.side_effect = _commit
    assert run_with_retry(db, fn, base_delay=0.001) == "done"
    assert n["fn"] == 2 and n["c"] == 2


def test_commit_with_retry_gives_up():
    db = MagicMock()
    db.commit.side_effect = OperationalError("COMMIT", {}, Exception("database is locked"))
    with pytest.raises(OperationalError):
        commit_with_retry(db, attempts=3, base_delay=0.001, redo=lambda: None)
