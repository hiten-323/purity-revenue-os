#!/usr/bin/env python3
"""One-shot trust/confidence sweep — no mass email, no AiSensy, no dials.

Usage (from backend/ on LIVE):
  python -m scripts.trust_confidence_sweep --limit 80
  python -m scripts.trust_confidence_sweep --limit 80 --no-verify
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# backend/ on sys.path when run as module or script
_HERE = os.path.abspath(os.path.dirname(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Fair trust/confidence sweep")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--no-verify", action="store_true",
                   help="Skip live MX/SMTP; use stored verification_status only")
    p.add_argument("--counts-only", action="store_true")
    args = p.parse_args(argv)

    from app.database.database import SessionLocal
    from app.services.trust_confidence import count_pool, run_limited_sweep

    db = SessionLocal()
    try:
        if args.counts_only:
            print(json.dumps(count_pool(db), indent=2))
            return 0
        out = run_limited_sweep(db, limit=args.limit, verify=not args.no_verify)
        # Drop per-lead sample noise for stdout summary
        summary = {k: v for k, v in out.items() if k != "results"}
        print(json.dumps(summary, indent=2, default=str))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
