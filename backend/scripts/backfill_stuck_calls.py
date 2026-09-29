"""One-off backfill for leads stuck in call_status='CALLING'.

DEFAULT IS A READ-ONLY DRY RUN. The database is opened with SQLite
``mode=ro`` so nothing can be written, and the script only prints what it
*would* change: counts by proposed terminal state, by outreach stage and by
age, plus a few sample decisions.

    python scripts/backfill_stuck_calls.py --db purity_beans.db
    python scripts/backfill_stuck_calls.py --db purity_beans.db --json out.json
    python scripts/backfill_stuck_calls.py --db purity_beans.db \
        --sidecar-export sidecar_calls.json          # evidence from Nuraveda

Writing requires BOTH ``--apply`` and ``--i-understand-this-writes`` and
should only ever be run by the founder after reviewing the dry run (and
after a backup). It never dials, never sends, never changes outreach_stage.

--sidecar-export is the JSON produced by
integrations/nuraveda-voice/export_call_evidence.mjs: a list of
{"lead_id": int, "disposition": "no_answer"|"busy"|"failed"|..., "user_turns": int,
 "sip_status": int}. It upgrades UNKNOWN_NO_RESULT to the
engine-observed disposition where the sidecar actually saw one.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load_sidecar(path: str | None) -> dict[int, dict]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for row in data if isinstance(data, list) else data.get("rows", []):
        try:
            lid = int(row.get("lead_id"))
        except (TypeError, ValueError):
            continue
        out[lid] = row  # last one wins: export is ordered oldest -> newest
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(BACKEND / "purity_beans.db"))
    ap.add_argument("--older-than-minutes", type=int, default=30)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sidecar-export", default=None)
    ap.add_argument("--json", default=None, help="also write the full report here")
    ap.add_argument("--samples", type=int, default=15)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-understand-this-writes", action="store_true")
    args = ap.parse_args(argv)

    os.environ.setdefault("AI_CALLING_ENABLED", "0")
    from sqlalchemy import create_engine

    from app.services.call_intelligence.reconciler import reconcile_stuck_calls

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    sidecar = _load_sidecar(args.sidecar_export)
    write = args.apply and args.i_understand_this_writes
    if args.apply and not write:
        print("--apply also needs --i-understand-this-writes; running dry run instead.")

    if not write:
        uri = f"sqlite:///file:{db_path.as_posix()}?mode=ro&uri=true"
        engine = create_engine(uri)
        with engine.connect() as conn:
            res = reconcile_stuck_calls(conn, dry_run=True,
                                        older_than_minutes=args.older_than_minutes,
                                        limit=args.limit, sidecar_evidence=sidecar,
                                        sample_size=args.samples)
        engine.dispose()
    else:
        from sqlalchemy.orm import sessionmaker

        from app.services.call_intelligence.models import ensure_call_results_table
        engine = create_engine(f"sqlite:///{db_path.as_posix()}",
                               connect_args={"timeout": 30})
        ensure_call_results_table(engine)
        db = sessionmaker(bind=engine)()
        try:
            res = reconcile_stuck_calls(db, dry_run=False,
                                        older_than_minutes=args.older_than_minutes,
                                        limit=args.limit, sidecar_evidence=sidecar,
                                        sample_size=args.samples)
        finally:
            db.close()
            engine.dispose()

    print(json.dumps({k: v for k, v in res.items() if k != "samples"}, indent=2, default=str))
    print("\nsample decisions:")
    for s in res.get("samples", []):
        print("  ", json.dumps(s, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
