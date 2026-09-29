"""Opener/script variants and epsilon-greedy selection.

A variant is an *angle* for the line after the statutory AI disclosure. The
disclosure itself (founder_call_pipeline.opening_for / script_discloses) is
never varied. Only founder-approved variants are ever selected:

  * ``baseline`` is always approved (it is today's agent behaviour -- the
    profile's own context line, no override);
  * any other variant must be listed in CALL_OPENER_VARIANTS_APPROVED
    (comma-separated ids).

Selection (per business type, falling back to all calls):
  1. never repeat an opener already used with this lead, if an alternative
     exists;
  2. while any candidate has fewer than CALL_LESSON_MIN_CONNECTED connected
     calls, explore: pick the least-sampled one (no fake confidence);
  3. otherwise exploit the best Wilson lower bound on interest rate, except
     with probability CALL_VARIANT_EPSILON (default 0.2) pick at random.
The RNG is seeded by lead id + attempt so a retry of the same dispatch is
deterministic and tests are reproducible.
"""
from __future__ import annotations

import os
import random
from collections import defaultdict
from typing import Any

from app.services.call_intelligence.lessons import min_connected
from app.services.call_intelligence.taxonomy import POSITIVE_OUTCOMES
from app.services.outreach_intelligence.experience_store import wilson_lower_bound

SCRIPT_VARIANT = "purity-coffee-b2b/v1"

# Only claims already permitted by founder_call_pipeline.CALL_CONSTRAINTS.
OPENER_VARIANTS: dict[str, dict[str, str]] = {
    "baseline": {
        "label": "Profile default context line",
        "instruction": "",
    },
    "question_first": {
        "label": "Ask about current coffee use before describing the product",
        "instruction": ("After the disclosure, do not pitch first. Ask one short "
                        "question about whether they use instant coffee today, "
                        "then describe Purity Beans only if they engage."),
    },
    "purity_claims": {
        "label": "Lead with the product facts",
        "instruction": ("After the disclosure, lead with the permitted product "
                        "facts in one sentence: 100% coffee, zero chicory, no "
                        "fillers. Then ask if it could be useful for them."),
    },
    "sample_offer": {
        "label": "Lead with a no-obligation catalogue/sample ask",
        "instruction": ("After the disclosure, offer to send the catalogue so they "
                        "can judge for themselves. Never promise a free sample, "
                        "a price or a delivery date."),
    },
}


def approved_variants() -> list[str]:
    raw = os.getenv("CALL_OPENER_VARIANTS_APPROVED", "")
    extra = [v.strip() for v in raw.split(",") if v.strip() in OPENER_VARIANTS]
    return ["baseline"] + [v for v in extra if v != "baseline"]


def epsilon() -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv("CALL_VARIANT_EPSILON", "0.2"))))
    except ValueError:
        return 0.2


def variant_stats(rows, business_type: str | None) -> dict[str, dict[str, Any]]:
    seg = [r for r in rows if business_type and r.business_type == business_type]
    use = seg if seg else list(rows)
    agg: dict[str, dict[str, int]] = defaultdict(lambda: {"connected": 0, "wins": 0, "attempts": 0})
    for r in use:
        v = r.opener_variant or "baseline"
        agg[v]["attempts"] += 1
        if r.connected:
            agg[v]["connected"] += 1
            if (r.outcome or "") in POSITIVE_OUTCOMES:
                agg[v]["wins"] += 1
    return {k: {**v, "wilson_lb": wilson_lower_bound(v["wins"], v["connected"])}
            for k, v in agg.items()}


def select_opener(*, lead_id: int | None, attempt: int, rows,
                  business_type: str | None, used_with_lead: list[str] | None = None,
                  approved: list[str] | None = None,
                  eps: float | None = None) -> dict[str, Any]:
    approved = approved or approved_variants()
    used = set(used_with_lead or [])
    cands = [v for v in approved if v not in used] or list(approved)
    stats = variant_stats(rows, business_type)
    rng = random.Random(f"{lead_id}:{attempt}")
    eps = epsilon() if eps is None else eps
    threshold = min_connected()

    def conn(v):
        return stats.get(v, {}).get("connected", 0)

    if len(cands) == 1:
        choice, mode = cands[0], "only_candidate"
    elif any(conn(v) < threshold for v in cands):
        low = min(conn(v) for v in cands)
        pool = [v for v in cands if conn(v) == low]
        choice, mode = rng.choice(sorted(pool)), "explore_below_threshold"
    elif rng.random() < eps:
        choice, mode = rng.choice(sorted(cands)), "explore_epsilon"
    else:
        choice = max(cands, key=lambda v: (stats[v]["wilson_lb"] or 0.0, v))
        mode = "exploit"
    return {
        "opener_variant": choice,
        "script_variant": SCRIPT_VARIANT,
        "instruction": OPENER_VARIANTS[choice]["instruction"],
        "mode": mode,
        "candidates": cands,
        "stats": {v: stats.get(v, {"connected": 0, "wins": 0, "attempts": 0, "wilson_lb": None})
                  for v in cands},
        "threshold_connected": threshold,
    }
