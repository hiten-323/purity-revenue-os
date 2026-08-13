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

# Paths that must never be in the index (tip).
FORBIDDEN_PATH_RES = [
    re.compile(r"(^|/)".replace("", "") + r".*\.env$", re.I),  # any .env
    re.compile(r"\.db$", re.I),
    re.compile(r"\.sqlite3?$", re.I),
    re.compile(r"removed_leads_backup\.json$", re.I),
]
# Allow .env.example only
ALLOWED_ENV = re.compile(r"(^|/)\.env\.example$")

# Heuristic patterns for live credentials in tracked tip content.
# History may still hold old values — that is why rotation is required.
# This scan only looks at the current tip blob contents.
SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("generic_api_key_assignment",
     re.compile(
         r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token"
         r"|password|private[_-]?key)\s*[=:]\s*['\"][^'\"]{12,}['\"]"
     )),
    ("bearer_token",
     re.compile(r"(?i)bearer\s+[a-z0-9._\-]{20,}")),
    ("private_key_block",
     re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

# Files that legitimately mention secret *names* (examples, docs).
SKIP_CONTENT_SCAN = {
    ".env.example",
    "backend/scripts/check_repo_hygiene.py",
    "backend/scripts/pre_rotation_gate.sh",
    "backend/scripts/post_rotation_gate.sh",
}


def tracked_files(repo_dir: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", repo_dir, "ls-files", "-z"],
        text=True,
    )
    return [p for p in out.split("\0") if p]


def main() -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(script_dir)
    repo_dir = os.path.dirname(backend_dir)

    files = tracked_files(repo_dir)
    bad_paths: list[str] = []
    for p in files:
        base = os.path.basename(p)
        if ALLOWED_ENV.search(p.replace("\\", "/")):
            continue
        for rx in FORBIDDEN_PATH_RES:
            # .env specifically
            if p.endswith(".env") or base == ".env":
                bad_paths.append(p)
                break
            if rx.search(p):
                bad_paths.append(p)
                break

    # Tighten: flag any path ending in .env that is not .env.example
    bad_paths = []
    for p in files:
        norm = p.replace("\\", "/")
        base = os.path.basename(norm)
        if base.endswith(".env") and base != ".env.example":
            bad_paths.append(p)
        if base.endswith((".db", ".sqlite", ".sqlite3")):
            bad_paths.append(p)
        if base == "removed_leads_backup.json":
            bad_paths.append(p)

    secret_hits: list[str] = []
    for p in files:
        norm = p.replace("\\", "/")
        if norm in SKIP_CONTENT_SCAN or os.path.basename(norm) in SKIP_CONTENT_SCAN:
            continue
        # Skip binary-ish and lockfiles
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
