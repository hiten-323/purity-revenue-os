"""The two touch points in the dial path (calling_agent + call_db_lock_patch).

prepare_dial(db, lead)  -> (refusal_reason | None, extra_context, brief, call_ref)
after_dial(db, lead, ...)  opens the CallResult row once the provider accepted.

Both are fail-open for *learning* (a brief that cannot be built never blocks
a call the pipeline allowed) and fail-closed for *standing rules* (a
do-not-call match always refuses).
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.call_intelligence.standing_rules import call_blocked

log = logging.getLogger(__name__)


def prepare_dial(db, lead) -> tuple[str | None, dict[str, Any], dict[str, Any] | None, str]:
    from app.services.call_intelligence.capture import new_call_ref

    call_ref = new_call_ref(getattr(lead, "id", None))
    blocked = call_blocked(lead)
    if blocked:
        return blocked, {}, None, call_ref
    try:
        from app.services.call_intelligence.brief import build_call_brief, dispatch_fields
        brief = build_call_brief(db, lead)
    except Exception as exc:  # noqa: BLE001
        log.warning("call brief unavailable for lead %s: %s", getattr(lead, "id", None), exc)
        return None, {"call_ref": call_ref}, None, call_ref
    if brief.get("skip"):
        return "call_brief_skip: " + "; ".join(brief.get("skip_reasons") or []), {}, brief, call_ref
    return None, dispatch_fields(brief, call_ref), brief, call_ref


def after_dial(db, lead, *, call_ref: str, brief: dict | None, provider: str | None,
               provider_call_id: str | None) -> None:
    try:
        from app.services.call_intelligence.capture import open_call_attempt
        open_call_attempt(
            db, lead, call_ref=call_ref, provider=provider,
            provider_call_id=provider_call_id,
            opener_variant=(brief or {}).get("opener_variant") or "baseline",
            script_variant=(brief or {}).get("script_variant"),
            brief=brief,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("open_call_attempt failed for lead %s: %s", getattr(lead, "id", None), exc)
