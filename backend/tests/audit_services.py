"""
Static audit for the defect classes this codebase has actually shipped.

Not a linter. Every rule here corresponds to a bug that reached production in
this project, so a hit is evidence rather than style opinion:

  RELAXED_GATE   `or email_verification_status == "VALID"` reappeared in three
                 separate modules after being removed. A column another process
                 can write is not a trust signal.
  SILENT_SWALLOW `except: pass` around real work. It hid the category engine
                 failing, so every draft silently fell back to a generic body.
  NONE_TO_ZERO   `x or 0` turns "never measured" into "measured zero". It fired
                 NO_WEBSITE_NO_REVIEWS on 582 never-looked-up leads.
  LOCAL_TIME     datetime.now() into a column every other write fills with UTC —
                 rows land 5.5 hours in the future for every comparison.
  INVENTED_DEFAULT  `.get(x, "Some Name")` — how "Procurement Manager" ended up
                 on every card and "Rajesh Kumar" before it.
  DUPLICATE_GATE Two modules deciding "is this sendable?" separately. They drift,
                 and the screen then advertises what the sender refuses.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

RULES = [
    ("RELAXED_GATE", re.compile(
        r'email_verification_status\s*==\s*"VALID"'),
     "trusts a column any process can write; use contact_trust.sendable()"),
    ("SILENT_SWALLOW", re.compile(
        r'except\s+Exception\s*:\s*\n\s*pass\s*$', re.M),
     "swallows the reason; log it or narrow the except"),
    ("NONE_TO_ZERO", re.compile(
        r'\b(reviews?|count|score|kg|consumption|opens|clicks)\w*\s+or\s+0\b', re.I),
     "coerces 'never measured' to 'measured zero'"),
    ("LOCAL_TIME", re.compile(r'datetime\.now\(\)'),
     "local time into a UTC column; use datetime.utcnow()"),
    # Only IDENTITY and PROVENANCE fields. A default status of DISCOVERED or a
    # priority of MEDIUM is a legitimate initial state — it asserts nothing about
    # the business. Defaulting the city, the source, or a person's name asserts a
    # FACT nobody supplied, which is what put 82 leads under a Google Maps
    # provenance they never had and a job title on every card.
    ("INVENTED_DEFAULT", re.compile(
        r"""\.get\(\s*["'](?:city|company|contact_name|decision_maker|"""
        r"""lead_source|source|email|phone|website|address|region|"""
        r"""contact_persona|contact_title)["']\s*,\s*["'][A-Za-z][^"']{2,}["']"""),
     "asserts a fact about the business that nobody supplied"),
]

# Files where a pattern is legitimate, with the reason. Anything not listed here
# is a finding.
ALLOW = {
    ("LOCAL_TIME", "business_policies.py"): "calling window is local by design",
    ("LOCAL_TIME", "crew_output_reader.py"): "display-only timestamp",
    ("LOCAL_TIME", "founder_brief.py"): "display-only timestamp",
    ("LOCAL_TIME", "gem_monitor.py"): "days-until countdown, local is correct",
    ("LOCAL_TIME", "timeutil.py"): "the IST conversion layer itself",
    # `(opens or 0) > 0` guards a None comparison and does not assert a
    # measurement — the branch it feeds prints "no open recorded", which is the
    # honest answer for a lead never looked at. The defect this rule exists for
    # is `or 0` that then gets PRESENTED as a measured zero.
    ("NONE_TO_ZERO", "endpoints.py"): (
        "guards a None comparison; the else-branch says 'no open recorded' "
        "rather than claiming a measured zero"),
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
            if ALLOW.get((name, p.name)):
                continue
            for m in rx.finditer(src):
                ln = src[:m.start()].count("\n") + 1
                text = lines[ln - 1].strip() if ln <= len(lines) else ""
                # Prose describing the bug is not the bug.
                if text.startswith(("#", '"', "'")) or "deliberately NOT" in text:
                    continue
                findings.append({"rule": name, "file": p.name, "line": ln,
                                 "code": text[:96], "why": why})
    return findings


def coordination() -> list[dict]:
    """
    Do the modules that answer the SAME question share one implementation?

    Divergence here is what produced "3 email ready" on a dashboard while the
    sender refused all three.
    """
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
        if len(items) > 6:
            print(f"      ... and {len(items)-6} more")
        print()

    print("COORDINATION — is one question answered in one place?\n")
    for c in coordination():
        print(f"  {c['shared_function']:20} used by {c['used_by']:2} module(s)")
        if c["modules"]:
            print(f"      {', '.join(c['modules'][:6])}")
    sys.exit(0 if not f else 1)
