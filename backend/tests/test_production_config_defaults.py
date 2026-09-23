"""Committed defaults keep non-worker outbound channels OFF; the sole worker may be armed."""
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


def test_ecosystem_arms_only_the_single_worker():
    text = _read("ecosystem.config.js")
    api = text[text.index('name: "purity-api"'):text.index('name: "purity-worker"')]
    worker = text[text.index('name: "purity-worker"'):text.index('name: "purity-beans"')]
    for block in (api, worker):
        assert 'SMART_OUTREACH_ENABLED: "0"' in block
        assert 'AISENSY_ENABLED: "0"' in block
        assert 'AI_CALLING_ENABLED: "0"' in block
    assert 'AUTO_OUTREACH_ENABLED: "0"' in api
    assert 'AUTO_OUTREACH_ENABLED: "1"' in worker


def test_ecosystem_resolves_nuraveda_dir_deterministically():
    text = _read("ecosystem.config.js")
    assert "resolveNuravedaDir" in text
    assert "NURAVEDA_DIR" in text
    assert "mustExist" in text
    assert "LIVEKIT_INIT_TIMEOUT_MS" in text
