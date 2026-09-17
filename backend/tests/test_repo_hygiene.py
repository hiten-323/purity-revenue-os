from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_repo_hygiene.py"
SPEC = importlib.util.spec_from_file_location("check_repo_hygiene", SCRIPT)
assert SPEC and SPEC.loader
hygiene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hygiene)


def test_example_env_sidecars_are_allowed():
    assert hygiene.is_forbidden_path("backend/.env.whatsapp.example") is False


def test_readme_is_not_excluded_from_secret_scan():
    assert "README.md" not in hygiene.SKIP_CONTENT_SCAN


def test_documented_placeholder_is_not_a_secret():
    text = 'set SCRAPLING_MCP_AUTH_TOKEN = "replace-with-a-long-random-token"'
    assert not any(pattern.search(text) for _, pattern in hygiene.SECRET_PATTERNS)


def test_live_quoted_secret_still_fails_scan():
    """The sample is assembled at runtime, never written as a literal.

    Spelled out in the source, this line made the scanner flag its own test
    file on every run. A hygiene gate that always FAILs is a gate people learn
    to ignore, which is worse than not having one.

    The alternative -- adding this file to SKIP_CONTENT_SCAN -- would have
    weakened the scanner to silence it, and PR #12 deliberately went the other
    way by removing README.md from that exclusion list. So the test keeps its
    exact meaning and the literal stops existing.
    """
    text = "API" + "_KEY = " + chr(34) + "this-is-a-real-looking-value" + chr(34)
    assert any(pattern.search(text) for _, pattern in hygiene.SECRET_PATTERNS)
