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

# 21 Oct: PM2 pins CALL_OPENER_* in its stored env, so a plain restart
# cannot change the experiment. Read these keys from backend/.env first.
def _cfg(key: str, default: str = "") -> str:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return os.environ.get(key, default)
    try:
        from dotenv import dotenv_values
        from pathlib import Path
        v = dotenv_values(Path(__file__).resolve().parents[3] / ".env").get(key)
        if v is not None:
            return v
    except Exception:
        pass
    return os.getenv(key, default)


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
                        "facts in one sentence: clean instant coffee in glass "
                        "jars, India's Cleanest Instant Coffee, no fillers. Never "
                        "say 100%. Pure coffee / no chicory only for Purica, Purista, Bold, Prima - never Ultra Blend. Then ask "
                        "if it could be useful for them."),
    },
    "sample_offer": {
        "label": "Lead with a no-obligation catalogue ask",
        "instruction": ("After the disclosure, offer to send the catalogue so they "
                        "can judge for themselves. Never offer samples, and never promise "
                        "a price or a delivery date."),
    },
    # Founder-approved E1 (2026-10-05): shorter spoken opener. Keeps statutory
    # AI disclosure + who/why in one short sentence + minute-ask only. No product
    # claims / rates / discounts before permission. Spoken text used when selected.
    "e1_short": {
        "label": "E1 shorter opener: disclosure + brief why + minute-ask only",
        "instruction": (
            "The spoken opening is already short (disclosure + brief why + minute ask). "
            "Do NOT add product claims, prices, factory rates, discounts, or certifications "
            "before they clearly give you a minute. After they agree, qualify briefly."
        ),
        "opening": (
            "Namaste, main Purity Beans ki taraf se AI assistant baat kar rahi hoon. "
            "Business coffee ke baare mein ek chhoti si baat karni thi - "
            "kya aapke paas ek minute hai?"
        ),
    },
    # E4 (founder-approved 2026-10-22): micro opener with persona name Vrini.
    # Disclosure (AI + Purity) kept; stock question only, no claims.
    "e4_micro": {
        "label": "E4 micro opener: Purity Beans se AI assistant Vrini + stock question",
        "instruction": (
            "The spoken opening already introduced you as Vrini, the Purity Beans AI assistant, "
            "and asked whether they stock instant coffee. Wait for the answer. Do NOT re-introduce "
            "yourself. If yes or maybe, offer the catalogue. Never offer samples. No prices, "
            "discounts, margins or certifications before they engage."
        ),
        "opening": "नमस्ते जी, Purity Beans से AI assistant Vrini। Instant coffee stock या use करते हैं?",
    },
    # E2 (founder-approved 2026-10-13): stock check. Disclosure + Purity Beans
    # instant coffee (glass jar, confirmed fact) + 20-second ask whether they
    # stock instant coffee. No distributor/brand relationship claimed.
    "e2_stock_check": {
        "label": "E2 stock-check opener: disclosure + product + 20-second stock question",
        "instruction": (
            "The spoken opening already asks whether they stock instant coffee. Wait for the answer. "
            "If yes, ask which brands sell best and offer the catalogue. Never offer samples. "
            "Do NOT claim any distributor or brand relationship, prices, discounts or certifications."
        ),
        "opening": (
            "Namaste, main Purity Beans ki AI assistant hoon. Hum glass jar mein instant coffee "
            "banate hain. Bas 20 second - kya aap instant coffee rakhte ya bechte hain?"
        ),
    },
    # E3 (founder-approved 2026-10-14): immediate hook, ~4 s. Disclosure first,
    # then the lead's city (when known), the stock question, one truthful
    # benefit. No margin / discount / offer figures (none confirmed), never
    # "100%", no chicory claim (Ultra Blend has chicory).
    "e3_hook": {
        "label": "E3 hook opener: disclosure + city + stock question + glass-jar benefit",
        "instruction": (
            "The spoken opening already asked whether they stock instant coffee. Wait for the answer. "
            "If yes or maybe, say we supply clean instant coffee in glass jars (India's Cleanest "
            "Instant Coffee) and offer the catalogue. Never offer samples. The ONLY confirmed figure "
            "is the distributor margin: lead with up to 30% (25% trade discount + 5% cash discount); only on "
            "margin pushback up to 35% max (25% trade discount + up to 10% cash discount, the extra "
            "cash discount for cash/advance payment). Only if this business is a "
            "distributor or wholesaler, never to supermarkets, retail or any other category. No other "
            "margin, discount, offer or price, never say 100%, never claim no chicory for Ultra Blend."
        ),
        "opening": (
            "Namaste, main Purity Beans ki AI assistant hoon. {city_q}{benefit}"
        ),
    },
}

_CITY_OK = __import__("re").compile(r"^[A-Za-z][A-Za-z .-]{1,24}$")


_DISTRIBUTOR_RE = __import__("re").compile(r"distribut|wholesal|stockist|super ?stockist|c ?& ?f", __import__("re").I)


def is_distributor(segment: str | None) -> bool:
    """14 Oct: the confirmed 30% margin applies to distributor/wholesaler leads only."""
    return bool(segment and _DISTRIBUTOR_RE.search(segment))


def _benefit(segment: str | None) -> str:
    if is_distributor(segment):
        return "Hum Purity Beans instant coffee range distributors ko 30% tak margin ke saath supply karte hain."
    return "Hum Purity Beans instant coffee range supply karte hain."


def _city_q(city: str | None) -> str:
    c = (city or "").strip().split(",")[0].strip()
    if c and _CITY_OK.match(c):
        return f"{c.title()} mein kya aap instant coffee rakhte hain? "
    return "Kya aap instant coffee rakhte hain? "



def opening_text(variant_id: str | None, city: str | None = None,
                 segment: str | None = None) -> str | None:
    """Spoken opening override for a variant, or None to use OPENING_DISCLOSURE.

    Only variants that define an ``opening`` key change what is heard first.
    Disclosure markers (AI + Purity) must remain in any opening text.
    """
    if not variant_id:
        return None
    entry = OPENER_VARIANTS.get(variant_id) or {}
    text = (entry.get("opening") or "").strip()
    if "{city_q}" in text:
        text = text.replace("{city_q}", _city_q(city))
    if "{benefit}" in text:
        text = text.replace("{benefit}", _benefit(segment))
    return text or None


# 4 Nov 2026 (Hiten "stop,fix all,resume" 14:14 IST, external audit): e4_micro
# on 100% of dials. baseline, e1_short and question_first are REMOVED from the
# rotation (they may not be re-enabled from .env); every selection returns
# PINNED_OPENER. Rollback: /opt/purity-revenue-os/backups/fix-b-e4micro-*/rollback.sh
PINNED_OPENER = "e4_micro"
REMOVED_VARIANTS = frozenset({"baseline", "e1_short", "question_first"})


def approved_variants() -> list[str]:
    raw = _cfg("CALL_OPENER_VARIANTS_APPROVED", "")
    extra = [v.strip() for v in raw.split(",")
             if v.strip() in OPENER_VARIANTS and v.strip() not in REMOVED_VARIANTS]
    return [PINNED_OPENER] + [v for v in extra if v != PINNED_OPENER]


def epsilon() -> float:
    try:
        return min(1.0, max(0.0, float(_cfg("CALL_VARIANT_EPSILON", "0.2"))))
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
    # 4 Nov 2026: pinned - e4_micro on every dial, whatever is passed in.
    approved = [PINNED_OPENER]
    used = set(used_with_lead or [])
    cands = [v for v in approved if v not in used] or list(approved)
    stats = variant_stats(rows, business_type)
    rng = random.Random(f"{lead_id}:{attempt}")
    eps = epsilon() if eps is None else eps
    threshold = min_connected()

    def conn(v):
        return stats.get(v, {}).get("connected", 0)

    # 21 Oct (founder-approved): a primary arm (E1 short opener) takes
    # CALL_OPENER_PRIMARY_SHARE of calls; the rest stay as control. Rollback:
    # remove CALL_OPENER_PRIMARY from .env (falls back to the bandit below).
    primary = (_cfg("CALL_OPENER_PRIMARY", "") or "").strip()
    try:
        share = min(1.0, max(0.0, float(_cfg("CALL_OPENER_PRIMARY_SHARE", "0.8"))))
    except ValueError:
        share = 0.8
    # 4 Nov 2026 (Hiten via B-B OUTREACH 08:08 IST): at a 100% primary share the
    # primary opener is used on every dial, even with a lead that already heard
    # it (a no-answer or a hang-up inside the greeting must not fall back to
    # baseline). Rollback: set CALL_OPENER_PRIMARY_SHARE below 1.
    if True:  # 4 Nov 2026: pinned, no bandit / control arm
        choice, mode = PINNED_OPENER, "pinned_e4_micro_100"
    elif primary and primary in approved and share >= 1.0:
        choice, mode = primary, "primary_arm_100"
    elif primary and primary in cands and len(cands) > 1:
        if rng.random() < share:
            choice, mode = primary, "primary_arm"
        else:
            others = sorted(v for v in cands if v != primary)
            choice, mode = rng.choice(others), "control_arm"
    elif len(cands) == 1:
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
        "opening": opening_text(choice),
        "mode": mode,
        "candidates": cands,
        "stats": {v: stats.get(v, {"connected": 0, "wins": 0, "attempts": 0, "wilson_lb": None})
                  for v in cands},
        "threshold_connected": threshold,
    }
