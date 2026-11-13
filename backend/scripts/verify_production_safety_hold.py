#!/usr/bin/env python3
"""Read-only production safety verifier: services online, all outbound held."""
from __future__ import annotations
import json, re, subprocess, sys, urllib.request
from pathlib import Path
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
ZERO = ("AUTO_WARM_ENABLED", "SMART_OUTREACH_ENABLED", "AUTO_OUTREACH_ENABLED",
        "AI_CALLING_ENABLED", "AUTO_AI_CALLING_ENABLED", "AISENSY_ENABLED",
        "EMAIL_SENDING_ENABLED", "KLAVIYO_WA_ENABLED", "KLAVIYO_WA_FLOW_LIVE",
        "WHATSAPP_PACER_ENABLED", "OUTREACH_AUTO_SEND")
ONE = ("AI_CALLING_KILL_SWITCH",)
REQUIRED_ONLINE = ("purity-api", "purity-worker", "purity-beans", "purity-tunnel",
                   "purity-outreach-report-bridge", "nuraveda-voice", "nuraveda-voice-agent",
                   "purity-cart-recovery", "purity-cod-verifier")

def env_file(path):
    out = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); out[k.strip()] = v.strip().strip("\"'")
    return out

def app_block(text, name):
    start = text.index('name: "' + name + '"')
    next_app = text.find('name: "', start + 1)
    return text[start: next_app if next_app >= 0 else len(text)]

def main():
    errors = []
    try:
        raw = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=20, check=True)
        apps = {x.get("name"): x for x in json.loads(raw.stdout)}
    except Exception as exc:
        print("FAIL pm2-list:", type(exc).__name__); return 2
    for name in REQUIRED_ONLINE:
        item = apps.get(name)
        if not item or item.get("pm2_env", {}).get("status") != "online": errors.append(name + " must be online")
    for name in ("purity-api", "purity-worker"):
        item = apps.get(name)
        if not item: continue
        effective = (item.get("pm2_env") or {}).get("env") or {}
        for key in ZERO:
            if str(effective.get(key, "")).strip().lower() not in ("0", "false", "no", "off"):
                errors.append("PM2 " + name + " " + key + " is not explicitly off")
        for key in ONE:
            if str(effective.get(key, "")).strip().lower() not in ("1", "true", "yes", "on"):
                errors.append("PM2 " + name + " " + key + " is not explicitly on")
    try:
        with urllib.request.urlopen("http://127.0.0.1:3104/health", timeout=8) as response:
            voice = json.loads(response.read())
        paused = set(voice.get("dispatch_paused_profiles") or [])
        if not voice.get("ok") or "purity-coffee-b2b" not in paused:
            errors.append("voice sidecar must keep purity-coffee-b2b profile paused")
    except Exception as exc: errors.append("voice health check failed: " + type(exc).__name__)
    try:
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10, check=True).stdout
        active = [x for x in cron.splitlines() if x.strip() and not x.lstrip().startswith("#") and "whatsapp_pacer_tick.py" in x]
        if active: errors.append("WhatsApp pacer cron must be disabled while provider is unconfigured")
    except Exception as exc: errors.append("crontab check failed: " + type(exc).__name__)
    values = env_file(BACKEND / ".env")
    for key in ZERO:
        if values.get(key, "").lower() not in ("0", "false", "no", "off"): errors.append(".env " + key + " is not explicitly off")
    for key in ONE:
        if values.get(key, "").lower() not in ("1", "true", "yes", "on"): errors.append(".env " + key + " is not explicitly on")
    if values.get("DISPATCH_PAUSED_PROFILES", "") != "purity-coffee-b2b": errors.append(".env B2B voice profile pause is missing")
    config = (ROOT / "ecosystem.config.js").read_text(encoding="utf-8")
    for name in ("purity-api", "purity-worker"):
        block = app_block(config, name)
        for key in ZERO:
            # Permit either a literal 0 or ENV.KEY || "0"; never a permissive 1 default.
            pat = r'(?m)^\s*' + re.escape(key) + r':\s*(?:ENV\.' + re.escape(key) + r'\s*\|\|\s*)?"0"\s*,?\s*$'
            if not re.search(pat, block): errors.append("ecosystem " + name + " " + key + " lacks a fail-closed 0 default")
    if errors:
        for item in errors: print("FAIL", item)
        return 1
    print("PASS production safety hold: all 9 services online; API and worker outbound flags off; AI kill switch on; B2B voice profile paused; WhatsApp pacer cron disabled")
    return 0
if __name__ == "__main__": raise SystemExit(main())
