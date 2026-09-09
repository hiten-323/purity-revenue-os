r"""
Read back what the engine decided, and why.

Every gate now records a line at the moment it decides, carrying the reason the
deciding code itself returned. This reads those lines and counts them, so
"nothing is going out" becomes a specific answer:

    1467  phone.eligibility  REFUSED  preference registry: no preference registry configured

rather than a shrug.

Reads the rotating files under backend/logs. It parses; it never invents. A
line it cannot parse is reported as unparsed rather than dropped, because a
silently discarded log entry is how the original problem started.

Usage
-----
    python scripts/decisions.py                     # summary, all components
    python scripts/decisions.py --component worker
    python scripts/decisions.py --step phone        # only phone.* decisions
    python scripts/decisions.py --verdict REFUSED
    python scripts/decisions.py --lead 1274         # one business's history
    python scripts/decisions.py --tail 40           # last 40 lines, verbatim
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

LINE = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d)\s+\w+\s+DECISION\s+"
    r"(?P<step>\S+)\s+(?:lead=(?P<lead>\d+))?\s*"
    r"(?P<verdict>[A-Z_]+)\s+(?P<reason>.*?)(?:\s+\[(?P<facts>.*)\])?$")


def _files(component: str):
    pattern = f"purity-{component}*.log*" if component else "purity-*.log*"
    return sorted(glob.glob(os.path.join(LOG_DIR, pattern)))


def _rows(files):
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    line = line.rstrip("\n")
                    if "DECISION" not in line:
                        continue
                    m = LINE.match(line)
                    yield (os.path.basename(path), n, line,
                           m.groupdict() if m else None)
        except OSError as exc:
            print(f"  could not read {path}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read the engine's decision log.")
    ap.add_argument("--component", default="", help="worker | api | demo")
    ap.add_argument("--step", default="", help="prefix filter, e.g. phone or email.send_gate")
    ap.add_argument("--verdict", default="", help="ALLOWED | REFUSED | SKIPPED | WAIT | STOP")
    ap.add_argument("--lead", type=int)
    ap.add_argument("--tail", type=int, default=0, help="print the last N matching lines verbatim")
    args = ap.parse_args()

    files = _files(args.component)
    if not files:
        print(f"No decision logs in {LOG_DIR}")
        print()
        print("They appear once a process that calls setup_logging() runs —")
        print("purity-worker or purity-api. Nothing is running right now if the")
        print("stack has not been started since the last reboot.")
        return 0

    matched, unparsed, pairs, leads = [], 0, Counter(), Counter()
    for fname, n, raw, g in _rows(files):
        if g is None:
            unparsed += 1
            continue
        if args.step and not g["step"].startswith(args.step):
            continue
        if args.verdict and g["verdict"] != args.verdict.upper():
            continue
        if args.lead and g["lead"] != str(args.lead):
            continue
        matched.append(raw)
        pairs[(g["step"], g["verdict"], (g["reason"] or "").strip()[:66])] += 1
        if g["lead"]:
            leads[g["lead"]] += 1

    print(f"decision lines matched : {len(matched)}   "
          f"from {len(files)} file(s) in {os.path.basename(LOG_DIR)}/")
    if unparsed:
        print(f"unparsed lines         : {unparsed}  (reported, not dropped)")
    print()

    if args.tail:
        for raw in matched[-args.tail:]:
            print(" ", raw)
        return 0

    if not matched:
        print("No decisions match that filter.")
        return 0

    print(f"{'count':>7}  {'step':<26} {'verdict':<9} reason")
    for (step, verdict, reason), n in pairs.most_common(30):
        print(f"{n:>7}  {step:<26} {verdict:<9} {reason}")

    if not args.lead and leads:
        print()
        print(f"businesses appearing        : {len(leads)}")
        print(f"most-decided lead ids       : "
              f"{', '.join(l for l, _ in leads.most_common(5))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
