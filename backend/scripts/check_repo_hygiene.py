#!/usr/bin/env python3
"""
Pre-rotation hygiene: tip must not track secrets or lead data.

Exit codes:
  0  clean
  1  tracked forbidden path and/or live-looking secret in tip
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ALLOWED_ENV = re.compile(r"(^|/)\.env\.example$")

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "generic_api_key_assignment",
        re.compile(
            r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token"
            r"|password|private[_-]?key)\s*[=:]\s*['\"][^'\"]{12,}['\"]"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)bearer\s+[a-z0-9._\-]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

SKIP_CONTENT_SCAN = {
    ".env.example",
    "backend/scripts/check_repo_hygiene.py",
    "backend/scripts/pre_rotation_gate.sh",
    "backend/scripts/post_rotation_gate.sh",
    "backend/scripts/verify_credentials.py",
}


def tracked_files(repo_dir: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", repo_dir, "ls-files", "-z"],
        text=True,
    )
    return [p for p in out.split("\0") if p]


def is_forbidden_path(p: str) -> bool:
    norm = p.replace("\\", "/")
    base = os.path.basename(norm)
    if ALLOWED_ENV.search(norm):
        return False
    # Live env and env sidecars (not .env.example)
    if base == ".env" or (base.startswith(".env.") and base != ".env.example"):
        return True
    if base.endswith(".env") and base != ".env.example":
        return True
    # DB and sidecars: purity.db, purity.db.bak, purity.db.before-...
    if base.endswith((".db", ".sqlite", ".sqlite3")):
        return True
    if ".db." in base or base.startswith("purity.db."):
        return True
    if base == "removed_leads_backup.json":
        return True
    return False


def main() -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(script_dir)
    repo_dir = os.path.dirname(backend_dir)

    files = tracked_files(repo_dir)
    bad_paths = [p for p in files if is_forbidden_path(p)]

    secret_hits: list[str] = []
    for p in files:
        norm = p.replace("\\", "/")
        if norm in SKIP_CONTENT_SCAN or os.path.basename(norm) in SKIP_CONTENT_SCAN:
            continue
        if norm.endswith((
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
            ".lock", ".sum",
        )):
            continue
        full = os.path.join(repo_dir, p)
        if not os.path.isfile(full):
            continue
        try:
            text = open(full, "r", encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for name, rx in SECRET_PATTERNS:
            if rx.search(text):
                secret_hits.append(f"{p}: {name}")

    ok = True
    if bad_paths:
        ok = False
        print("FAIL: forbidden paths still tracked:")
        for p in bad_paths:
            print(f"  - {p}")
    else:
        print("PASS: no .env / *.db / lead-export tracked at tip")

    if secret_hits:
        ok = False
        print("FAIL: secret-like patterns in tip files:")
        for h in secret_hits[:30]:
            print(f"  - {h}")
        if len(secret_hits) > 30:
            print(f"  ... and {len(secret_hits) - 30} more")
    else:
        print("PASS: secret scan of tip files clean")

    print(
        "NOTE: git history may still hold old credentials — "
        "that is why rotation is required after this gate."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
