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
    text = 'API_KEY = "this-is-a-real-looking-secret-value"'
    assert any(pattern.search(text) for _, pattern in hygiene.SECRET_PATTERNS)
