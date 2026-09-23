"""Production defaults keep autonomous outreach on the single worker executor; safety channels remain off."""
from __future__ import annotations

import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_env_example_keeps_outreach_and_calling_off():
    text = _read("backend/.env.example")
    for flag in (
        "SMART_OUTREACH_ENABLED=0",
        "AUTO_OUTREACH_ENABLED=0",
        "AISENSY_ENABLED=0",
        "AI_CALLING_ENABLED=0",
    ):
        assert flag in text, flag
    assert "SMART_OUTREACH_ENABLED=1" not in text
    assert "AUTO_OUTREACH_ENABLED=1" not in text
    assert "AISENSY_ENABLED=1" not in text
    assert re.search(r"^AI_CALLING_ENABLED=1", text, re.M) is None


def test_ecosystem_has_one_autonomous_executor_and_safe_channels():
    text = _read("ecosystem.config.js")
    assert 'name: "purity-outreach"' not in text
    assert 'script: path.join(BACKEND_DIR, "smart_outreach_worker.py")' not in text
    assert 'SMART_OUTREACH_ENABLED: "0"' in text
    assert 'AUTO_OUTREACH_ENABLED: "0"' in text
    assert 'AISENSY_ENABLED: "0"' in text
    assert 'AI_CALLING_ENABLED: "0"' in text


def test_worker_uses_auto_outreach_as_sole_send_switch():
    text = _read("backend/worker.py")
    assert 'os.getenv("AUTO_OUTREACH_ENABLED"' in text
    assert 'os.getenv("SMART_OUTREACH_ENABLED"' not in text


def test_ecosystem_resolves_nuraveda_dir_deterministically():
    text = _read("ecosystem.config.js")
    assert "resolveNuravedaDir" in text
    assert "NURAVEDA_DIR" in text
    assert "mustExist" in text
    assert "LIVEKIT_INIT_TIMEOUT_MS" in text
