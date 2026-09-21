"""personal_test_call.py may only dial 9855593323 with founder flags."""
from __future__ import annotations

import ast
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "personal_test_call.py"


def test_script_exists_and_allowlists_only_one_number():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "9855593323" in src
    assert "ALLOWED_TAILS" in src
    assert "--i-am-the-founder" in src
    assert "--confirm" in src


def test_allowlist_is_a_singleton():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "ALLOWED_TAILS":
                    value = ast.literal_eval(node.value)
                    assert set(value) == {"9855593323"}
                    return
    raise AssertionError("ALLOWED_TAILS not found")


def test_live_path_requires_founder_flags():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "i_am_the_founder" in src
    assert "DRY RUN" in src
    assert "SMART_OUTREACH_ENABLED" in src
    assert "AISENSY_ENABLED" in src
