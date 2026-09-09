"""
Logging that nobody configured is logging nobody reads.

Fourteen modules called logging.getLogger(__name__) and logged at INFO or
DEBUG. Nothing configured a handler, so the root logger had zero handlers and
an effective level of WARNING, and every one of those lines was discarded --
including email_sender's record of WHY a send was blocked. worker.py called
basicConfig so the worker printed something; run_server.py did not, so the API
printed nothing. Two processes, two realities, neither documented.

These tests pin that a configured process actually emits, that the helpers
cannot break the code they observe, and that both entry points configure.
"""
from __future__ import annotations

import logging
import os

import pytest

from app import observability as obs

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _reset():
    """setup_logging is deliberately idempotent, which makes it sticky across
    tests. Reset the module flag and root handlers around each one."""
    obs._configured = False
    root = logging.getLogger()
    saved = list(root.handlers)
    for h in saved:
        root.removeHandler(h)
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved:
        root.addHandler(h)
    obs._configured = False


def test_setup_installs_a_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "LOG_DIR", str(tmp_path))
    obs.setup_logging("test")
    assert logging.getLogger().handlers, "root still has no handler"


def test_info_actually_reaches_a_handler(tmp_path, monkeypatch, caplog):
    """The original defect: INFO was below the effective level and vanished."""
    monkeypatch.setattr(obs, "LOG_DIR", str(tmp_path))
    obs.setup_logging("test")
    assert logging.getLogger("app.services.email_sender").isEnabledFor(logging.INFO)


def test_it_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "LOG_DIR", str(tmp_path))
    obs.setup_logging("unit")
    obs.decision("email.send_gate", obs.REFUSED, "no address on file", lead=7)
    logging.shutdown()

    written = list(tmp_path.glob("purity-unit.log"))
    assert written, "no log file created"
    body = written[0].read_text(encoding="utf-8")
    assert "email.send_gate" in body
    assert "lead=7" in body
    assert "no address on file" in body


def test_calling_setup_twice_does_not_duplicate_output(tmp_path, monkeypatch):
    monkeypatch.setattr(obs, "LOG_DIR", str(tmp_path))
    obs.setup_logging("test")
    n = len(logging.getLogger().handlers)
    obs.setup_logging("test")
    assert len(logging.getLogger().handlers) == n


def test_a_broken_log_dir_does_not_stop_the_process(tmp_path, monkeypatch):
    """A gate must not fail because a log file cannot be opened."""
    monkeypatch.setattr(obs, "LOG_DIR", str(tmp_path / "nested" / "\0bad"))
    obs.setup_logging("test")          # must not raise
    obs.decision("x.y", obs.REFUSED, "reason")


def test_decision_never_raises():
    """Not configured, odd arguments — still silent, still safe."""
    obs.decision("x.y", obs.REFUSED, None)
    obs.decision("x.y", obs.ALLOWED, "ok", lead=object())
    obs.step("x.y", "doing a thing", count=None)


def test_verdicts_are_a_small_fixed_set():
    """So `grep REFUSED | sort | uniq -c` is a real answer."""
    verdicts = {obs.ALLOWED, obs.REFUSED, obs.SKIPPED, obs.WAIT, obs.STOP, obs.DONE}
    assert len(verdicts) == 6
    assert all(v.isupper() for v in verdicts)


def test_both_entry_points_configure_logging():
    """run_server.py had no logging setup at all, so the API discarded
    everything below WARNING."""
    for name in ("run_server.py", "worker.py"):
        src = open(os.path.join(BACKEND, name), encoding="utf-8").read()
        assert "setup_logging(" in src, f"{name} does not configure logging"
