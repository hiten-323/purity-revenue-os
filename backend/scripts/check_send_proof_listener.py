#!/usr/bin/env python3
"""
Prove send_proof_fix replaced the fail-open EMAIL_SENT listener.

Exit codes:
  0  strict listener active, old listener gone
  1  strict missing and/or old still registered
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import app.models.models  # noqa: F401
    import app.models.send_proof_fix  # noqa: F401
    from sqlalchemy import event
    from app.models.models import WorkflowEvent, _require_send_proof

    strict = event.contains(
        WorkflowEvent,
        "before_insert",
        app.models.send_proof_fix._require_send_proof_strict,
    )
    old = event.contains(WorkflowEvent, "before_insert", _require_send_proof)

    print(f"strict_listener_registered: {strict}")
    print(f"old_listener_still_registered: {old}")

    if not strict or old:
        print("FAIL: send_proof_fix not applied")
        return 1

    print("PASS: fail-closed send-proof is the active listener")
    return 0


if __name__ == "__main__":
    sys.exit(main())
