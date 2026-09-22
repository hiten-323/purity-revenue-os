"""Outcome hierarchy + classifiers. Distinguishes OBSERVED / INFERRED / LOW_CONFIDENCE."""
from __future__ import annotations
from typing import Any

OUTCOME_LEVELS = {
    0: "SENT_OR_DIALLED",
    1: "DELIVERED_OR_RINGING",
    2: "OPENED_OR_ANSWERED",
    3: "REPLIED_OR_ENGAGED",
    4: "POSITIVE_INTEREST",
    5: "COMMERCIAL_SIGNAL",
    6: "CALLBACK_OR_MEETING",
    7: "SAMPLE_OR_PROPOSAL_PROGRESS",
    8: "REPEAT_OR_WON",
}

EMAIL_CLASSES = {
    "NO_RESPONSE", "POSITIVE", "NEGATIVE", "QUESTION", "PRICE_REQUEST",
    "CATALOGUE_REQUEST", "SAMPLE_REQUEST", "CALLBACK_REQUEST",
    "NOT_RELEVANT", "UNSUBSCRIBE", "BOUNCE",
}

_INTENT_MAP = {
    "INTERESTED": "POSITIVE", "POSITIVE": "POSITIVE",
    "NOT_INTERESTED": "NEGATIVE", "NEGATIVE": "NEGATIVE",
    "UNSUBSCRIBE": "UNSUBSCRIBE", "OPT_OUT": "UNSUBSCRIBE", "DO_NOT_CONTACT": "UNSUBSCRIBE",
    "BOUNCE": "BOUNCE", "HARD_BOUNCE": "BOUNCE", "EMAIL_BOUNCED": "BOUNCE",
    "PRICE": "PRICE_REQUEST", "PRICING": "PRICE_REQUEST", "PRICING_REQUESTED": "PRICE_REQUEST",
    "SEND_PRICING": "PRICE_REQUEST",
    "CATALOGUE": "CATALOGUE_REQUEST", "CATALOGUE_REQUESTED": "CATALOGUE_REQUEST",
    "SEND_DETAILS": "CATALOGUE_REQUEST",
    "SAMPLE": "SAMPLE_REQUEST", "SAMPLE_REQUESTED": "SAMPLE_REQUEST",
    "CALLBACK": "CALLBACK_REQUEST", "CALL_LATER": "CALLBACK_REQUEST",
    "MEETING_REQUESTED": "CALLBACK_REQUEST",
    "QUESTION": "QUESTION", "NOT_RELEVANT": "NOT_RELEVANT", "WRONG_PERSON": "NOT_RELEVANT",
}

_COMMERCIAL = {"PRICE_REQUEST", "CATALOGUE_REQUEST", "SAMPLE_REQUEST", "CALLBACK_REQUEST"}
_POS_CALL = {
    "INTERESTED", "SEND_DETAILS", "SEND_PRICING", "SAMPLE_REQUESTED",
    "MEETING_REQUESTED", "CALLBACK", "PRICE_OBJECTION", "EXISTING_SUPPLIER",
}


def _conf(kind: str | None) -> str:
    k = (kind or "").upper()
    return k if k in {"OBSERVED", "INFERRED", "LOW_CONFIDENCE"} else "LOW_CONFIDENCE"


def outcome_level(
    *,
    channel: str | None = None,
    email_class: str | None = None,
    call_outcome: str | None = None,
    delivery_status: str | None = None,
    signals: dict | None = None,
) -> int:
    ec = (email_class or "").upper()
    co = (call_outcome or "").upper()
    ds = (delivery_status or "").upper()
    sig = signals or {}

    if ec == "BOUNCE" or co in {"NO_ANSWER", "FAILED", "BUSY"}:
        return 0
    if co in {"ORDER_WON", "REORDER"}:
        return 8
    if co == "SAMPLE_SENT" or (sig.get("sample_requested") and co == "SAMPLE_SENT"):
        return 7
    if ec == "CALLBACK_REQUEST" or co in {"CALLBACK", "MEETING_REQUESTED", "CALL_LATER"} or sig.get("meeting_requested") or sig.get("callback_requested"):
        return 6
    if ec in _COMMERCIAL or co in {"SEND_PRICING", "SEND_DETAILS", "SAMPLE_REQUESTED", "PRICE_OBJECTION"} or sig.get("price_discussed") or sig.get("catalogue_requested") or sig.get("sample_requested"):
        return 5
    if ec == "POSITIVE" or co in _POS_CALL or sig.get("interested"):
        return 4
    if ec in {"QUESTION", "NEGATIVE", "NOT_RELEVANT", "UNSUBSCRIBE"} or co in {"NOT_INTERESTED", "WRONG_PERSON", "GATEKEEPER", "DO_NOT_CONTACT"}:
        return 3
    if ds in {"READ", "OPENED", "ANSWERED"} or co in {"ANSWERED", "HUMAN_ANSWERED"}:
        return 2
    if ds in {"DELIVERED", "PROVIDER_ACCEPTED"} or co in {"RINGING", "DIALLED"}:
        return 1
    if ds == "SENT" or (channel or "").lower() in {"email", "call"}:
        return 0
    return 0


def classify_email_reply(
    *,
    intent: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    raw = (intent or "").strip().upper()
    et = (event_type or "").strip().upper()
    text = f"{subject or ''} {body or ''}".lower()

    if et in {"EMAIL_BOUNCED", "HARD_BOUNCE"} or "bounce" in text:
        return {"class": "BOUNCE", "level": 0, "confidence": _conf("OBSERVED" if et else "INFERRED"), "source": "event_or_text"}

    if raw in _INTENT_MAP:
        cls = _INTENT_MAP[raw]
        return {"class": cls, "level": outcome_level(channel="email", email_class=cls), "confidence": "OBSERVED", "source": "intent"}

    rules = [
        ("UNSUBSCRIBE", ("unsubscribe", "stop emailing", "remove me", "opt out")),
        ("SAMPLE_REQUEST", ("sample", "tasting", "trial pack")),
        ("CATALOGUE_REQUEST", ("catalogue", "catalog", "brochure", "price list", "product list")),
        ("PRICE_REQUEST", ("price", "pricing", "rate", "quote")),
        ("CALLBACK_REQUEST", ("call me", "callback", "call back", "meeting")),
        ("NEGATIVE", ("not interested", "no thanks", "do not contact", "don't contact")),
        ("QUESTION", ("?", "how much", "do you", "can you")),
        ("POSITIVE", ("interested", "sounds good", "please send", "yes")),
    ]
    for cls, needles in rules:
        if any(n in text for n in needles):
            return {"class": cls, "level": outcome_level(channel="email", email_class=cls), "confidence": "INFERRED", "source": "keyword"}

    if not raw and not text.strip():
        return {"class": "NO_RESPONSE", "level": 0, "confidence": "LOW_CONFIDENCE", "source": "empty"}
    return {"class": "QUESTION" if text.strip() else "NO_RESPONSE", "level": 3 if text.strip() else 0, "confidence": "LOW_CONFIDENCE", "source": "fallback"}


def extract_call_commercial_signals(
    *,
    outcome: str | None = None,
    transcript: str | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    out = (outcome or payload.get("outcome") or payload.get("call_outcome") or "").strip().upper()
    blob = f"{transcript or ''} {summary or ''} {payload.get('summary') or ''}".lower()
    signals = {
        "interested": False, "price_discussed": False, "catalogue_requested": False,
        "sample_requested": False, "callback_requested": False, "meeting_requested": False,
        "not_interested": False, "do_not_contact": False, "gatekeeper": False,
        "existing_supplier": False, "raw_outcome": out or None,
    }
    confidence, source = "LOW_CONFIDENCE", "none"
    if out:
        confidence, source = "OBSERVED", "outcome"
        if out in _POS_CALL:
            signals["interested"] = True
        if out in {"SEND_PRICING", "PRICE_OBJECTION"}:
            signals["price_discussed"] = True
        if out == "SEND_DETAILS":
            signals["catalogue_requested"] = True
        if out == "SAMPLE_REQUESTED":
            signals["sample_requested"] = True
        if out in {"CALLBACK", "CALL_LATER"}:
            signals["callback_requested"] = True
        if out == "MEETING_REQUESTED":
            signals["meeting_requested"] = True
        if out == "NOT_INTERESTED":
            signals["not_interested"] = True
        if out == "DO_NOT_CONTACT":
            signals["do_not_contact"] = True
        if out == "GATEKEEPER":
            signals["gatekeeper"] = True
        if out == "EXISTING_SUPPLIER":
            signals["existing_supplier"] = True
    inferred = False
    if "sample" in blob:
        signals["sample_requested"] = True; inferred = True
    if any(w in blob for w in ("price", "pricing", "rate", "quote")):
        signals["price_discussed"] = True; inferred = True
    if any(w in blob for w in ("catalogue", "catalog", "brochure")):
        signals["catalogue_requested"] = True; inferred = True
    if any(w in blob for w in ("call back", "callback", "call me later")):
        signals["callback_requested"] = True; inferred = True
    if "not interested" in blob:
        signals["not_interested"] = True; inferred = True
    if inferred and confidence != "OBSERVED":
        confidence, source = "INFERRED", "transcript_or_summary"
    return {
        "signals": signals,
        "level": outcome_level(channel="call", call_outcome=out, signals=signals),
        "confidence": confidence,
        "source": source,
    }
