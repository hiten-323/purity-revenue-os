"""
Static audit for the defect classes this codebase has actually shipped.

Not a linter. Every rule here corresponds to a bug that reached production in
this project, so a hit is evidence rather than style opinion.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

RULES = [
    ("RELAXED_GATE", re.compile(r'email_verification_status\s*==\s*"VALID"'),
     "trusts a column any process can write; use contact_trust.sendable()"),
    ("SILENT_SWALLOW", re.compile(r'except\s+Exception\s*:\s*\n\s*pass\s*$', re.M),
     "swallows the reason; log it or narrow the except"),
    ("NONE_TO_ZERO", re.compile(r'\b(reviews?|count|score|kg|consumption|opens|clicks)\w*\s+or\s+0\b', re.I),
     "coerces never measured to measured zero"),
    ("LOCAL_TIME", re.compile(r'datetime\.now\(\)'),
     "local time into a UTC column; use datetime.utcnow()"),
    ("INVENTED_DEFAULT", re.compile(
        r"""\.get\(\s*["'](?:city|company|contact_name|decision_maker|"""
        r"""lead_source|source|email|phone|website|address|region|"""
        r"""contact_persona|contact_title)["']\s*,\s*["'][A-Za-z][^"']{2,}["']"""),
     "asserts a fact about the business that nobody supplied"),
]

ALLOW = {
    ("LOCAL_TIME", "business_policies.py"),
    ("LOCAL_TIME", "crew_output_reader.py"),
    ("LOCAL_TIME", "founder_brief.py"),
    ("LOCAL_TIME", "gem_monitor.py"),
    ("LOCAL_TIME", "timeutil.py"),
    ("NONE_TO_ZERO", "endpoints.py"),
}


def audit() -> list[dict]:
    findings = []
    for p in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lines = src.splitlines()
        for name, rx, why in RULES:
            if (name, p.name) in ALLOW:
                continue
            for m in rx.finditer(src):
                ln = src[:m.start()].count("\n") + 1
                text = lines[ln - 1].strip() if ln <= len(lines) else ""
                if text.startswith(("#", '"', "'")) or "deliberately NOT" in text:
                    continue
                findings.append({"rule": name, "file": p.name, "line": ln,
                                 "code": text[:96], "why": why})
    return findings


def coordination() -> list[dict]:
    out = []
    users = {"sendable": [], "actionable": [], "verify_email": [],
             "pitch_for": [], "check_send_allowed": []}
    for p in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        for fn in users:
            if re.search(rf"\b{fn}\s*\(", src) and f"def {fn}" not in src:
                users[fn].append(p.name)
    for fn, mods in users.items():
        out.append({"shared_function": fn, "used_by": len(mods),
                    "modules": sorted(set(mods))})
    return out


def test_audit_has_expected_shape():
    findings = audit()
    assert isinstance(findings, list)
    coordination_rows = coordination()
    assert {row["shared_function"] for row in coordination_rows} == {
        "sendable", "actionable", "verify_email", "pitch_for", "check_send_allowed"
    }


if __name__ == "__main__":
    f = audit()
    print("SERVICE AUDIT — defect classes this codebase has actually shipped\n")
    if not f:
        print("  no findings")
    by = {}
    for x in f:
        by.setdefault(x["rule"], []).append(x)
    for rule, items in sorted(by.items(), key=lambda kv: -len(kv[1])):
        print(f"  {rule}  ({len(items)})  — {items[0]['why']}")
        for i in items[:6]:
            print(f"      {i['file']}:{i['line']}  {i['code']}")
        print()
    print("COORDINATION — is one question answered in one place?\n")
    for c in coordination():
        print(f"  {c['shared_function']:20} used by {c['used_by']:2} module(s)")
        if c["modules"]:
            print(f"      {', '.join(c['modules'][:6])}")
    sys.exit(0 if not f else 1)
