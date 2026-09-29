"""Rule-based 'how did the call go' classifier.

Extends outreach_intelligence.outcomes.extract_call_commercial_signals (PR #33)
with Hindi/Hinglish/Punjabi phrases, objections, decision-maker reach,
drop-off point, language, sentiment and a rudeness/complaint flag.

Never invents data:
  * the provider/agent outcome is OBSERVED and wins;
  * a transcript rule may only fill a gap (UNKNOWN / hang-up-without-outcome)
    or refine a generic INTERESTED into SAMPLE/CATALOGUE when the *caller's*
    words say so -- always marked INFERRED with the matched phrase as evidence;
  * with no transcript, fields stay None rather than guessed.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from app.services.call_intelligence.taxonomy import (
    CONNECTED_OUTCOMES, ENGINE_TERMINATIONS, NOT_CONNECTED_OUTCOMES,
    POSITIVE_OUTCOMES, result_outcome_for,
)

# --------------------------------------------------------------- lexicon ----
# Lower-cased substrings. Devanagari entries are matched on the raw text.
OUTCOME_PHRASES: dict[str, tuple[str, ...]] = {
    "DO_NOT_CALL": (
        "don't call", "dont call", "do not call", "stop calling", "never call",
        "call mat", "phone mat", "dobara call", "dubara call", "call na kare",
        "call na karein", "mat karo call", "कॉल मत", "फोन मत", "दोबारा कॉल",
    ),
    "WRONG_PERSON": (
        "wrong number", "galat number", "not the owner", "owner nahi",
        "main owner nahi", "malik nahi", "not the right person",
        "गलत नंबर", "मालिक नहीं",
    ),
    "NOT_INTERESTED": (
        "not interested", "no interest", "interest nahi", "interested nahi",
        "nahi chahiye", "nahin chahiye", "zarurat nahi", "zaroorat nahi",
        "need nahi", "no need", "नहीं चाहिए", "ज़रूरत नहीं", "जरूरत नहीं",
        "रुचि नहीं", "नहीं चाहीदा", "ਨਹੀਂ ਚਾਹੀਦਾ",
    ),
    "SAMPLE_REQUESTED": ("sample", "सैंपल", "ਸੈਂਪਲ", "trial pack", "tasting"),
    "CATALOGUE_REQUESTED": (
        "catalogue", "catalog", "brochure", "rate list", "price list",
        "rate card", "details bhej", "detail bhej", "whatsapp pe bhej",
        "whatsapp kar", "send details", "send me details", "कैटलॉग",
    ),
    "CALLBACK_REQUESTED": (
        "call back", "callback", "call later", "call me later", "baad mein call",
        "baad me call", "kal call", "shaam ko call", "subah call",
        "बाद में कॉल", "कल कॉल", "phir call",
    ),
    "INTERESTED": (
        "interested", "haan bhejo", "haan ji bhejo", "theek hai bhejo",
        "zaroor", "bilkul", "sounds good", "हाँ भेजो", "ज़रूर",
    ),
}

OBJECTION_PHRASES: dict[str, tuple[str, ...]] = {
    "PRICE": ("price", "rate kya", "mehenga", "mehnga", "costly", "expensive",
              "sasta", "कीमत", "महंगा", "discount"),
    "EXISTING_SUPPLIER": ("already have", "already use", "supplier hai",
                          "pehle se", "pahle se", "existing supplier",
                          "nescafe", "bru ", "पहले से", "सप्लायर"),
    "NO_NEED": ("no need", "zarurat nahi", "zaroorat nahi", "coffee nahi",
                "don't use coffee", "we don't use", "chai hi", "ज़रूरत नहीं"),
    "TIMING": ("busy", "abhi nahi", "abhi time nahi", "baad mein", "later",
               "व्यस्त", "अभी नहीं"),
    "NOT_DECISION_MAKER": ("owner nahi", "not the owner", "manager se",
                           "sir nahi", "boss se", "malik se", "owner se baat",
                           "मालिक से", "decision nahi"),
    "TRUST_AI": ("robot", "machine hai", "ai hai kya", "recorded", "fraud",
                 "spam", "scam", "रोबोट"),
    "MOQ": ("minimum order", "moq", "kitna lena", "quantity"),
}

RUDE_PHRASES = (
    "bakwas", "pagal", "idiot", "stupid", "shut up", "bewakoof", "harass",
    "complaint", "spam", "fraud", "scam", "police", "block kar", "gaali",
    "बकवास", "पागल",
)
POSITIVE_WORDS = ("thank", "dhanyavaad", "dhanyawad", "shukriya", "accha",
                  "achha", "badhiya", "great", "good", "haan", "zaroor",
                  "धन्यवाद", "अच्छा", "ਧੰਨਵਾਦ")
NEGATIVE_WORDS = ("not interested", "nahi chahiye", "don't call", "stop",
                  "bakwas", "pagal", "busy", "no", "nahi")
DM_YES_PHRASES = ("main owner", "i am the owner", "i'm the owner", "main hi dekhta",
                  "main hi dekhti", "main malik", "i handle purchase",
                  "purchase main dekhta", "मैं मालिक", "मैं ही देखता")
_HINGLISH = ("hai", "nahi", "kya", "aap", "ji", "haan", "theek", "bhej", "mein", "karo")
_SESSION_CLOSED_RE = re.compile(r"session closed after (\d+) turn", re.I)


def _norm_turns(turns: Iterable[dict] | None, transcript: str | None) -> list[dict]:
    out: list[dict] = []
    for i, t in enumerate(turns or []):
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or "").lower()
        text = str(t.get("text") or "")
        if role not in {"user", "assistant", "tool"}:
            continue
        out.append({"role": role, "text": text,
                    "turn_index": t.get("turn_index", i)})
    if out or not transcript:
        return out
    # "user: ...\nassistant: ..." style transcripts carry roles; anything else
    # stays unattributed and is only used as a text blob.
    for i, line in enumerate(str(transcript).splitlines()):
        m = re.match(r"\s*(user|caller|customer|lead|assistant|agent|ai|bot)\s*[:\-]\s*(.*)", line, re.I)
        if not m:
            continue
        who = m.group(1).lower()
        role = "user" if who in {"user", "caller", "customer", "lead"} else "assistant"
        out.append({"role": role, "text": m.group(2), "turn_index": i})
    return out


def _find(text: str, phrases: Iterable[str]) -> str | None:
    low = text.lower()
    for p in phrases:
        if p and (p in low or p in text):
            return p
    return None


def detect_language(user_text: str, hint: str | None = None) -> str | None:
    if hint:
        return str(hint)[:12]
    if not user_text.strip():
        return None
    if re.search(r"[\u0A00-\u0A7F]", user_text):
        return "pa-IN"
    if re.search(r"[\u0900-\u097F]", user_text):
        return "hi-IN"
    words = re.findall(r"[a-z]+", user_text.lower())
    if words and sum(w in _HINGLISH for w in words) / len(words) >= 0.12:
        return "hi-Latn"
    return "en-IN" if words else None


def classify_call(*, fsm_key: str | None = None,
                  termination_reason: str | None = None,
                  turns: Iterable[dict] | None = None,
                  transcript: str | None = None,
                  summary: str | None = None,
                  details: dict | None = None,
                  language_hint: str | None = None,
                  answered: bool | None = None,
                  opener_variant: str | None = None) -> dict[str, Any]:
    details = details or {}
    key = (fsm_key or "").strip().upper()
    term = (termination_reason or "").strip().upper() or None
    norm = _norm_turns(turns, transcript)
    user_turns = [t for t in norm if t["role"] == "user" and t["text"].strip()]
    user_text = " \n".join(t["text"] for t in user_turns)
    blob = user_text if norm else f"{transcript or ''}"
    evidence: dict[str, Any] = {"rules": []}

    # Close-handler FAILED: livekit-agent.js only reports it after the callee
    # answered (lead_id is bound on participant join), so it is a hang-up.
    if key == "FAILED" and not term and summary and _SESSION_CLOSED_RE.search(summary):
        term = "HANGUP_NO_OUTCOME"
        evidence["rules"].append("summary:session_closed_after_answer")

    if term and not key:
        key = ENGINE_TERMINATIONS.get(term, "FAILED")

    outcome = result_outcome_for(key) if key else "UNKNOWN"
    confidence = "OBSERVED" if key else "LOW_CONFIDENCE"

    connected: bool | None
    if outcome in CONNECTED_OUTCOMES or key == "OTHER":
        connected = True
    elif term in {"HANGUP_NO_OUTCOME", "COMPLETED_NO_OUTCOME"} or (
            outcome == "FAILED" and user_turns):
        connected = True
    elif outcome in NOT_CONNECTED_OUTCOMES:
        connected = False
    else:
        connected = True if (answered or user_turns) else None
    if answered is True and connected is None:
        connected = True

    # Gap-filling from the caller's own words.
    hung_up_connected = connected and (outcome in {"FAILED", "UNKNOWN"})
    if hung_up_connected and blob.strip():
        for cand in ("DO_NOT_CALL", "WRONG_PERSON", "NOT_INTERESTED",
                     "SAMPLE_REQUESTED", "CATALOGUE_REQUESTED",
                     "CALLBACK_REQUESTED", "INTERESTED"):
            hit = _find(blob, OUTCOME_PHRASES[cand])
            if hit:
                outcome, confidence = cand, "INFERRED"
                evidence["rules"].append(f"transcript:{cand}")
                evidence["matched"] = hit
                break
        else:
            outcome, confidence = "UNKNOWN", "LOW_CONFIDENCE"
    elif hung_up_connected:
        outcome = "UNKNOWN"
        confidence = "INFERRED" if "summary:session_closed_after_answer" in evidence["rules"] else "LOW_CONFIDENCE"
    elif outcome == "INTERESTED" and user_text:
        for cand in ("SAMPLE_REQUESTED", "CATALOGUE_REQUESTED"):
            hit = _find(user_text, OUTCOME_PHRASES[cand])
            if hit:
                outcome, confidence = cand, "INFERRED"
                evidence["rules"].append(f"refine:{cand}")
                evidence["matched"] = hit
                break
    if key == "MEETING_REQUESTED":
        evidence["rules"].append("fsm:MEETING_REQUESTED->INTERESTED")

    # Objections: agent-reported enum first (observed), then caller's words.
    objections: list[str] = []
    rep = str(details.get("objection") or "").strip().upper()
    if rep and rep not in {"NONE", "OTHER", ""}:
        objections.append(rep)
    for name, phrases in OBJECTION_PHRASES.items():
        if name not in objections and blob.strip() and _find(blob, phrases):
            objections.append(name)

    # Decision maker.
    dm_rep = str(details.get("decision_maker") or "").strip().upper()
    if dm_rep == "YES":
        dm: bool | None = True
    elif dm_rep == "NO" or outcome == "WRONG_PERSON" or "NOT_DECISION_MAKER" in objections:
        dm = False
    elif blob.strip() and _find(blob, DM_YES_PHRASES):
        dm = True
        evidence["rules"].append("transcript:dm_yes")
    else:
        dm = None
    if connected is False:
        dm = None

    rude_hit = _find(blob, RUDE_PHRASES) if blob.strip() else None
    rude = bool(rude_hit) if blob.strip() else None
    if rude_hit:
        evidence["rude_matched"] = rude_hit

    # Sentiment (caller only).
    sentiment: str | None = None
    if user_text.strip():
        low = user_text.lower()
        pos = sum(w in low for w in POSITIVE_WORDS)
        neg = sum(w in low for w in NEGATIVE_WORDS) + (3 if rude else 0)
        if outcome in POSITIVE_OUTCOMES:
            pos += 2
        if outcome in {"NOT_INTERESTED", "DO_NOT_CALL"}:
            neg += 2
        sentiment = "POSITIVE" if pos > neg else ("NEGATIVE" if neg > pos else "NEUTRAL")
    elif connected is False:
        sentiment = None

    # Drop-off point.
    turn_count = len(norm) if norm else None
    user_turn_count = len(user_turns) if norm else None
    drop_turn = norm[-1]["turn_index"] if norm else None
    if connected is False:
        stage = "RINGING"
    elif connected is None:
        stage = None
    elif outcome in POSITIVE_OUTCOMES:
        stage = "CLOSE"
    elif user_turn_count is None:
        stage = "OPENER" if term == "HANGUP_NO_OUTCOME" else None
    elif user_turn_count <= 1:
        stage = "OPENER"
    elif user_turns and any(_find(user_turns[-1]["text"], p) for p in OBJECTION_PHRASES.values()):
        stage = "OBJECTION"
    elif user_turn_count <= 3:
        stage = "DISCOVERY"
    else:
        stage = "PITCH"

    language = detect_language(user_text, language_hint)

    lessons = derive_lessons(outcome=outcome, connected=connected, stage=stage,
                             objections=objections, dm=dm, rude=rude,
                             opener_variant=opener_variant)
    return {
        "outcome": outcome, "raw_outcome": key or None,
        "termination_reason": term, "confidence": confidence,
        "connected": connected, "reached_decision_maker": dm,
        "objections": objections, "drop_off_turn": drop_turn,
        "drop_off_stage": stage, "turn_count": turn_count,
        "user_turn_count": user_turn_count, "language": language,
        "sentiment": sentiment, "rude_or_complaint": rude,
        "lessons": lessons, "evidence": evidence,
    }


def derive_lessons(*, outcome: str, connected: bool | None, stage: str | None,
                   objections: list[str], dm: bool | None, rude: bool | None,
                   opener_variant: str | None) -> dict[str, list[str]]:
    worked: list[str] = []
    failed: list[str] = []
    v = opener_variant or "baseline"
    if outcome in POSITIVE_OUTCOMES:
        worked.append(f"opener '{v}' reached {outcome}")
        for o in objections:
            worked.append(f"objection {o} came up and the call still ended {outcome}")
        if dm:
            worked.append("reached the decision maker")
    if connected and outcome not in POSITIVE_OUTCOMES:
        if stage == "OPENER":
            failed.append(f"call ended during the opener ('{v}') - get to the point faster")
        for o in objections:
            failed.append(f"unresolved objection: {o}")
        if dm is False:
            failed.append("spoke to a non-decision-maker - ask for the owner/purchase person early")
    if rude:
        failed.append("caller reacted negatively/complained - do not retry")
    if connected is False:
        failed.append("did not connect")
    return {"worked": worked, "failed": failed}


def plain_summary(result: dict[str, Any], agent_summary: str | None = None) -> str:
    if agent_summary and not _SESSION_CLOSED_RE.search(agent_summary):
        return agent_summary.strip()[:1000]
    out = result.get("outcome") or "UNKNOWN"
    if result.get("connected") is False:
        return {"NO_ANSWER": "No answer.", "BUSY": "Line busy.",
                "VOICEMAIL": "Went to voicemail.",
                "FAILED": "Call failed before connecting."}.get(out, "Did not connect.")
    parts = ["Answered"]
    if result.get("reached_decision_maker") is True:
        parts.append("by the decision maker")
    elif result.get("reached_decision_maker") is False:
        parts.append("by a non-decision-maker")
    stage = result.get("drop_off_stage")
    if out == "UNKNOWN":
        parts.append(f"; call ended at {stage.lower() if stage else 'an unknown point'} with no outcome recorded")
    else:
        parts.append(f"; outcome {out.replace('_', ' ').lower()}")
    if result.get("objections"):
        parts.append("; objections: " + ", ".join(result["objections"]).lower())
    return ("".join([parts[0] + (" " + parts[1] if len(parts) > 1 and not parts[1].startswith(";") else "")]
                    + [p for p in parts[1:] if p.startswith(";")]) + ".")[:1000]
