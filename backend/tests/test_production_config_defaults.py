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
        "AUTO_AI_CALLING_ENABLED=0",
    ):
        assert flag in text, flag
    assert "SMART_OUTREACH_ENABLED=1" not in text
    assert "AUTO_OUTREACH_ENABLED=1" not in text
    assert "AISENSY_ENABLED=1" not in text
    assert "AUTO_AI_CALLING_ENABLED=1" not in text
    assert re.search(r"^AI_CALLING_ENABLED=1", text, re.M) is None
    # The new policy knobs are documented as defaults, not left implicit.
    assert "AI_CALL_BUSINESS_HOURS=1" in text
    # Fleet-wide volume caps are gone (no rolling-24h / daily budget / per
    # cycle lead-count limit): unlimited eligible-lead processing inside the
    # business-hours window, per-lead gates only.
    assert "AI_CALLING_ROLLING_24H_CAP" not in text
    assert "AUTO_AI_CALLING_LIMIT" not in text


def test_ecosystem_arms_only_the_single_worker():
    text = _read("ecosystem.config.js")
    api = text[text.index('name: "purity-api"'):text.index('name: "purity-worker"')]
    worker = text[text.index('name: "purity-worker"'):text.index('name: "purity-beans"')]
    for block in (api, worker):
        assert 'SMART_OUTREACH_ENABLED: "0"' in block
        assert 'AISENSY_ENABLED: "0"' in block
    assert 'AUTO_OUTREACH_ENABLED: "0"' in api
    assert 'AUTO_OUTREACH_ENABLED: ENV.AUTO_OUTREACH_ENABLED || "0"' in worker
    # Only the worker may call. The API must explicitly keep both the
    # calling capability and scheduler off, even when .env arms the worker.
    assert 'AI_CALLING_ENABLED: "0"' in api
    assert 'AUTO_AI_CALLING_ENABLED: "0"' in api
    assert 'AI_CALLING_ENABLED: ENV.AI_CALLING_ENABLED || "0"' in worker
    assert 'AUTO_AI_CALLING_ENABLED: ENV.AUTO_AI_CALLING_ENABLED || "0"' in worker

def test_readiness_gate_matches_worker_only_calling_policy():
    text = (ROOT / "scripts/production_readiness_windows.ps1").read_text(encoding="utf-16")
    assert "F2-api-scheduler-off" in text
    assert "J-worker-calling-on" in text
    assert "K-worker-scheduler-on" in text
    assert "J-worker-calling-off" not in text
    assert "L-voice-health" in text
    assert "L-voice-outcome-quality" in text
    assert "L-voice-inflight" in text
    assert "M-voice-dry-run" in text
    assert "M2-livekit-sip-api" in text
    assert "N-personal-live-call" in text
    sip = (ROOT.parent / "ai-voice-agent/scripts/verify_livekit_sip.mjs").read_text(encoding="utf-8")
    assert "listSipOutboundTrunk" in sip
    assert "process.exitCode = 1" in sip
    sidecar_server = (ROOT.parent / "ai-voice-agent/src/server.js").read_text(encoding="utf-8")
    assert "PROCESS_STARTED_AT" in sidecar_server
    assert "failedSinceStart" in sidecar_server
    assert "doneSinceStart" in sidecar_server
    dry_run = _read("backend/scripts/production_voice_dry_run.py")
    assert "AUTO_AI_CALLING_ENABLED" in dry_run
    assert "no call placed" in dry_run


def test_ecosystem_resolves_nuraveda_dir_deterministically():
    text = _read("ecosystem.config.js")
    assert "resolveNuravedaDir" in text
    assert "NURAVEDA_DIR" in text
    assert "mustExist" in text
    assert "LIVEKIT_INIT_TIMEOUT_MS" in text

def test_pm2_runtime_verifier_checks_effective_worker_only_policy():
    text = _read("backend/scripts/verify_pm2_flags.py")
    assert '"purity-api"' in text
    assert '"AUTO_AI_CALLING_ENABLED": "0"' in text
    assert '"purity-worker"' in text
    assert '"AUTO_AI_CALLING_ENABLED": "1"' in text
    assert '"AISENSY_ENABLED": "0"' in text
    assert '"nuraveda-voice-agent"' in text
    assert 'subprocess.run' in text
    assert 'No process is restarted' in text

