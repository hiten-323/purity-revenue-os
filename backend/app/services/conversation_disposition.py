"""
Structured Conversation Disposition Classifier for Purity Revenue OS.

Classifies raw transcripts, call outcomes, WhatsApp messages, or email replies
into one of the 8 definitive commercial dispositions:
1. POSITIVE (Interested, catalogue, quote, sample, MOQ, distributor terms)
2. CALLBACK_REQUESTED (Call tomorrow, call at 4 PM, call next Monday, etc.)
3. INFORMATION_REQUESTED (Send product details, share prices, catalogue via WhatsApp/email)
4. DECISION_PENDING (Discuss with partner, review pricing, decide next week)
5. NEGATIVE (Not interested, already have supplier)
6. OPT_OUT_DNC (Do not call me, remove my number, stop messaging)
7. INVALID_CONTACT (Wrong number, not associated with business, invalid email)
8. NO_ANSWER (No answer, voicemail, busy, technical fail)
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Any

log = logging.getLogger("conversation_disposition")

DISPOSITION_POSITIVE = "POSITIVE"
DISPOSITION_CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
DISPOSITION_INFORMATION_REQUESTED = "INFORMATION_REQUESTED"
DISPOSITION_DECISION_PENDING = "DECISION_PENDING"
DISPOSITION_NEGATIVE = "NEGATIVE"
DISPOSITION_OPT_OUT_DNC = "OPT_OUT_DNC"
DISPOSITION_INVALID_CONTACT = "INVALID_CONTACT"
DISPOSITION_NO_ANSWER = "NO_ANSWER"


def classify_conversation(
    transcript: str = "",
    outcome: str = "",
    summary: str = "",
    channel: str = "call"
) -> Dict[str, Any]:
    """
    Analyzes customer transcript, outcome code, and summary to produce a structured disposition.
    """
    text = f"{outcome} {summary} {transcript}".lower().strip()
    out_upper = (outcome or "").upper().strip()

    res = {
        "disposition": DISPOSITION_NO_ANSWER,
        "confidence": 0.5,
        "reason": "Default disposition",
        "action_required": "adaptive_retry",
        "extracted_data": {},
    }

    # 1. OPT-OUT / DNC (Highest precedence compliance check)
    dnc_patterns = [
        r"do\s+not\s+call", r"don't\s+call", r"remove\s+(?:my\s+)?number",
        r"stop\s+calling", r"stop\s+messaging", r"unsubscribe",
        r"mat\s+call\s+karo", r"number\s+hatao", r"complaint",
        r"dnc\b", r"opt[ -]?out\b", r"never\s+call"
    ]
    if out_upper in ("DNC", "DO_NOT_CONTACT", "OPT_OUT", "OPTED_OUT") or any(re.search(p, text) for p in dnc_patterns):
        res["disposition"] = DISPOSITION_OPT_OUT_DNC
        res["confidence"] = 1.0
        res["reason"] = "Explicit opt-out / DNC request detected"
        res["action_required"] = "immediate_suppression"
        return res

    # 2. INVALID CONTACT
    invalid_patterns = [
        r"wrong\s+number", r"galat\s+number", r"not\s+(?:the\s+)?(?:correct|associated)\s+person",
        r"no\s+such\s+person", r"not\s+running\s+(?:a\s+)?business", r"invalid\s+(?:number|email)"
    ]
    if out_upper in ("WRONG_NUMBER", "INVALID_NUMBER", "SIP_INVALID") or any(re.search(p, text) for p in invalid_patterns):
        res["disposition"] = DISPOSITION_INVALID_CONTACT
        res["confidence"] = 0.95
        res["reason"] = "Wrong number or invalid contact endpoint"
        res["action_required"] = "suppress_endpoint"
        return res

    # 3. CALLBACK REQUESTED
    callback_patterns = [
        r"call\s+(?:me\s+)?(?:back\s+)?tomorrow", r"call\s+(?:at|around)\s+\d",
        r"call\s+(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday)",
        r"kal\s+(?:call|baat)", r"shaam\s+ko\s+call", r"subah\s+call",
        r"call\s+after", r"traveling", r"busy\s+right\s+now", r"in\s+a\s+meeting"
    ]
    if out_upper in ("CALLBACK_REQUESTED", "CALLBACK", "CALL_ME_LATER") or any(re.search(p, text) for p in callback_patterns):
        res["disposition"] = DISPOSITION_CALLBACK_REQUESTED
        res["confidence"] = 0.9
        res["reason"] = "Prospect requested a callback at a future time"
        res["action_required"] = "schedule_callback"
        return res

    # 4. DECISION PENDING
    decision_patterns = [
        r"discuss\s+with\s+(?:my\s+)?(?:partner|team|boss|director)",
        r"review\s+(?:the\s+)?(?:pricing|rates|quotation|quote)",
        r"decide\s+next\s+week", r"soch\s+ke\s+batata", r"baat\s+karke\s+bataunga",
        r"let\s+you\s+know\s+(?:later|next\s+week)"
    ]
    if out_upper in ("DECISION_PENDING", "THINKING_ABOUT_IT") or any(re.search(p, text) for p in decision_patterns):
        res["disposition"] = DISPOSITION_DECISION_PENDING
        res["confidence"] = 0.85
        res["reason"] = "Prospect pending commercial review or partner discussion"
        res["action_required"] = "schedule_decision_followup"
        return res

    # 5. INFORMATION REQUESTED
    info_patterns = [
        r"send\s+(?:me\s+)?(?:the\s+)?(?:details|info|information|brochure)",
        r"share\s+(?:the\s+)?prices", r"rate\s+list\s+bhejiye",
        r"whatsapp\s+(?:pe\s+)?(?:bhejo|share\s+karo)", r"email\s+me\s+details"
    ]
    if out_upper in ("INFO_REQUESTED", "INFORMATION_REQUESTED", "BROCHURE_REQUESTED") or any(re.search(p, text) for p in info_patterns):
        res["disposition"] = DISPOSITION_INFORMATION_REQUESTED
        res["confidence"] = 0.9
        res["reason"] = "Prospect requested product/pricing info via messaging"
        res["action_required"] = "dispatch_info_authorized_channel"
        return res

    # 6. POSITIVE (High-intent conversion)
    positive_patterns = [
        r"interested", r"send\s+catalog(?:ue)?", r"send\s+quotation", r"send\s+quote",
        r"need\s+a\s+sample", r"interested\s+in\s+sample", r"discuss\s+moq",
        r"distributor\s+terms", r"wholesale\s+rates?", r"order\s+karna\s+hai"
    ]
    if out_upper in ("QUALIFIED", "WHATSAPP_OPT_IN", "CATALOGUE_REQUESTED", "SAMPLE_REQUESTED", "ORDER_CREATED") or any(re.search(p, text) for p in positive_patterns):
        res["disposition"] = DISPOSITION_POSITIVE
        res["confidence"] = 0.95
        res["reason"] = "Positive buying interest or catalogue/quotation requested"
        res["action_required"] = "advance_pipeline_and_quote"
        return res

    # 7. NEGATIVE
    negative_patterns = [
        r"not\s+interested", r"nahi\s+chahiye", r"already\s+have\s+(?:a\s+)?supplier",
        r"already\s+have\s+(?:a\s+)?vendor", r"don't\s+need\s+coffee", r"no\s+requirement"
    ]
    if out_upper in ("NOT_INTERESTED", "REFUSED", "DECLINED") or any(re.search(p, text) for p in negative_patterns):
        res["disposition"] = DISPOSITION_NEGATIVE
        res["confidence"] = 0.95
        res["reason"] = "Prospect explicitly not interested or has existing supplier"
        res["action_required"] = "stop_sales_sequence"
        return res

    # 8. NO ANSWER (Default for unanswered calls or empty interactions)
    res["disposition"] = DISPOSITION_NO_ANSWER
    res["confidence"] = 0.8
    res["reason"] = f"No definitive customer response (outcome={out_upper or 'NONE'})"
    res["action_required"] = "adaptive_retry"
    return res
