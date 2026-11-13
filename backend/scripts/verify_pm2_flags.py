"""Read-only verification of effective PM2 process flags on Windows.

Exit codes: 0 all required checks pass; 1 a policy mismatch is found;
2 PM2 cannot be queried or its output cannot be verified.
No process is restarted and no outbound activity is initiated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PM2_CMD = os.environ.get("PM2_CMD", "pm2")
EXPECTED = {
    "purity-api": {
        "AI_CALLING_ENABLED": "0",
        "AUTO_AI_CALLING_ENABLED": "0",
        "AUTO_OUTREACH_ENABLED": "0",
        "AISENSY_ENABLED": "0",
    },
    "purity-worker": {
        "AI_CALLING_ENABLED": "1",
        "AUTO_AI_CALLING_ENABLED": "1",
        "AUTO_OUTREACH_ENABLED": "1",
        "AISENSY_ENABLED": "0",
    },
}
REQUIRED_ONLINE = (
    "purity-api",
    "purity-worker",
    "purity-beans",
    "purity-tunnel",
    "purity-outreach-report-bridge",
    "nuraveda-voice",
    "nuraveda-voice-agent",
)


def main() -> int:
    command = f'"{PM2_CMD}" jlist'
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=35,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"UNVERIFIED PM2 query failed: {type(exc).__name__}")
        return 2
    if result.returncode != 0 or not result.stdout.strip():
        print("UNVERIFIED PM2 jlist returned no usable process list")
        return 2
    try:
        processes = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        print("UNVERIFIED PM2 process list is not valid JSON")
        return 2

    by_name = {item.get("name"): item for item in processes if item.get("name")}
    failed = False
    unverified = False
    for name in REQUIRED_ONLINE:
        item = by_name.get(name)
        if item is None:
            print(f"FAIL process-{name}: missing from PM2")
            failed = True
            continue
        env = item.get("pm2_env") or {}
        status = env.get("status", "unknown")
        if status != "online":
            print(f"FAIL process-{name}: status={status}")
            failed = True
        else:
            print(f"PASS process-{name}: online")

    for app, expected_flags in EXPECTED.items():
        item = by_name.get(app)
        if item is None:
            continue
        env = item.get("pm2_env") or {}
        effective = env.get("env") or {}
        for flag, expected in expected_flags.items():
            actual = effective.get(flag)
            if actual is None:
                print(f"UNVERIFIED {app}-{flag}: not present in effective PM2 environment")
                unverified = True
            elif str(actual).strip() != expected:
                print(f"FAIL {app}-{flag}: expected {expected}, got {str(actual).strip()}")
                failed = True
            else:
                print(f"PASS {app}-{flag}: {expected}")

    if failed:
        return 1
    if unverified:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
