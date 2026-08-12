"""
Read what the buyer actually wrote, and draft the answer to THAT.

WHY
A reply is the most expensive thing outreach produces and the easiest to waste.
The default behaviour — fire the next scheduled follow-up — answers a question
nobody asked and tells the buyer nobody read their mail. Worse, a generic
follow-up sent after a real reply is the single fastest way to lose a warm
account.

So: parse the reply, classify what they want, and prepare the specific response
BEFORE the founder opens it. The draft waits for approval; it never sends
itself. The founder's job becomes reading one prepared answer rather than
composing from scratch.

WHAT IS CLASSIFIED, AND FROM WHAT
Intent comes from the buyer's own words, and the evidence is kept — the phrase
that triggered the classification travels with the draft, so a wrong reading is
visible rather than mysterious. Where the text is genuinely ambiguous the intent
is UNCLEAR and the draft says so, because guessing at a buyer's meaning and
answering confidently is worse than asking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Intent:
    key: str
    label: str
    patterns: tuple
    next_action: str
    urgency: str
    guidance: str
    needs_founder: bool = False       # a pricing or commercial call is not AI work


# Ordered: the first match wins, so a stronger buying signal outranks a weaker
# one when a reply contains both ("send pricing and a sample" -> sample).
INTENTS: list[Intent] = [
    Intent("ORDER", "Wants to order",
           (r"\bplace an order\b", r"\bwant to order\b", r"\bpurchase order\b",
            r"\bhow do (?:i|we) (?:buy|order)\b", r"\bproforma\b"),
           "PREPARE_PROFORMA", "high",
           "They are buying. Confirm quantities, packs and delivery, then invoice.",
           needs_founder=True),
    Intent("SAMPLE", "Wants a sample",
           (r"\bsample\b", r"\btrial pack\b", r"\btry (?:it|the product)\b",
            r"\bsend.{0,20}\btaste\b"),
           "DISPATCH_SAMPLE", "high",
           "Confirm the shipping address and which blends. Dispatch is physical "
           "— it is only marked done when it actually goes."),
    Intent("PRICING", "Wants pricing",
           (r"\bprice list\b", r"\bpricing\b", r"\brates?\b", r"\bquotation\b",
            r"\bquote\b", r"\bcost\b", r"\bhow much\b", r"\bper (?:kg|jar|unit)\b"),
           "SEND_PRICING", "high",
           "Quote against their volumes. MRP is published and uniform; the "
           "buying price follows quantity.",
           needs_founder=True),
    Intent("MEETING", "Wants to meet or talk",
           (r"\bmeet(?:ing)?\b", r"\bvisit\b", r"\bcall me\b", r"\bcatch up\b",
            r"\bschedule\b", r"\bappointment\b", r"\bdiscuss.{0,20}person\b"),
           "MEETING_BRIEF", "high",
           "Confirm a time and prepare the brief before it."),
    Intent("CATALOGUE", "Wants the catalogue or details",
           (r"\bcatalogu?e\b", r"\bbrochure\b", r"\bproduct (?:list|range|details)\b",
            r"\bmore (?:details|information|info)\b", r"\bspecifications?\b"),
           "SEND_CATALOGUE", "medium",
           "Send the range with the packs relevant to their category."),
    Intent("EXISTING_SUPPLIER", "Already has a supplier",
           (r"\balready (?:have|buy|work|deal|sourc)", r"\bexisting supplier\b",
            r"\bcurrent(?:ly)? (?:buy|using|sourc)", r"\btied? up with\b",
            r"\bnescaf|bru\b|\bcontinental\b"),
           "FOUNDER_CALL", "medium",
           "Not a refusal. Ask what they pay and what they would change; offer a "
           "comparison rather than a switch."),
    Intent("NOT_INTERESTED", "Not interested",
           (r"\bnot interested\b", r"\bno (?:thanks|thank you)\b",
            r"\bdo not (?:contact|email)\b", r"\bunsubscribe\b",
            r"\bremove (?:me|us)\b", r"\bstop (?:emailing|sending)\b"),
           "CLOSE", "none",
           "Close it. No further automated outreach.",),
    Intent("LATER", "Interested but not now",
           (r"\blater\b", r"\bnext (?:month|quarter|year|week)\b", r"\bafter\b",
            r"\bnot (?:right )?now\b", r"\bcurrently (?:not|no)\b",
            r"\bseason\b", r"\bfestival\b"),
           "FOLLOW_UP_SCHEDULED", "low",
           "Their timing wins. Diarise it and stop everything generic meanwhile."),
    Intent("QUESTION", "Asked a question",
           (r"\?", r"\bwhat (?:is|are)\b", r"\bdo you\b", r"\bcan you\b",
            r"\bis it\b", r"\bwhich\b"),
           "ANSWER_QUESTION", "medium",
           "Answer what was asked, directly, before pitching anything further."),
]

# Phrases that mean the mail is not from a human at all.
_AUTO = (r"out of office", r"auto[- ]?reply", r"automatic reply",
         r"delivery status notification", r"undeliverable", r"mail delivery",
         r"do not reply", r"noreply", r"vacation")


def classify(text: str) -> dict:
    """
    What does this reply want? Returns the intent, the phrase that decided it,
    and how confident that is.
    """
    body = (text or "").strip()
    low = body.lower()
    if not body:
        return {"intent": "EMPTY", "label": "empty reply", "evidence": None,
                "confidence": "none", "needs_founder": True}

    for p in _AUTO:
        m = re.search(p, low)
        if m:
            return {"intent": "AUTO_REPLY", "label": "automated response",
                    "evidence": m.group(0), "confidence": "high",
                    "next_action": None, "urgency": "none",
                    "guidance": ("Not a human reply — do not treat it as "
                                 "engagement and do not advance the stage."),
                    "needs_founder": False}

    hits = []
    for it in INTENTS:
        for p in it.patterns:
            m = re.search(p, low)
            if m:
                hits.append((it, m.group(0)))
                break

    if not hits:
        return {"intent": "UNCLEAR", "label": "could not be classified",
                "evidence": None, "confidence": "none",
                "next_action": "FOUNDER_READ", "urgency": "medium",
                "guidance": ("The reply does not match a known intent. Read it "
                             "yourself — guessing at a buyer's meaning and "
                             "answering confidently is worse than asking."),
                "needs_founder": True}

    it, ev = hits[0]
    return {"intent": it.key, "label": it.label, "evidence": ev,
            # One clear signal is high; several competing ones mean the reply is
            # doing more than one thing and the founder should see it.
            "confidence": "high" if len(hits) == 1 else "medium",
            "also_matched": [h[0].key for h in hits[1:]],
            "next_action": it.next_action, "urgency": it.urgency,
            "guidance": it.guidance, "needs_founder": it.needs_founder}


# ── Drafting the answer to what they actually said ───────────────────────────

_OPENERS = {
    "SAMPLE":     "Thank you for coming back to me — I will get a sample out to you.",
    "PRICING":    "Thank you for coming back to me. Pricing below.",
    "CATALOGUE":  "Thank you for coming back to me — our range is below.",
    "MEETING":    "Thank you for coming back to me. Happy to meet.",
    "ORDER":      "Thank you — I will get this moving straight away.",
    "EXISTING_SUPPLIER": "Thank you for telling me — that is useful to know.",
    "LATER":      "Thank you for coming back to me, and understood on timing.",
    "QUESTION":   "Thank you for coming back to me.",
}


def draft_reply(lead, reply_text: str, classification: dict,
                memory: dict | None = None, signature: str = "") -> dict:
    """
    Compose the answer to THIS reply. Prepared, never sent — it waits for
    founder approval like every other outbound.
    """
    from app.services.outreach_engine import pitch_for, SKUS, UnmappedCategory

    mem = memory or {}
    intent = classification.get("intent", "UNCLEAR")
    dm = (mem.get("decision_maker") or "").strip()
    greet = f"Dear {dm}," if dm else "Dear Sir/Madam,"

    try:
        p = pitch_for(getattr(lead, "division", "") or "")
    except UnmappedCategory:
        p = None

    lines = [greet, "", _OPENERS.get(intent, "Thank you for coming back to me."), ""]

    # Quote their own words back, so the reply visibly answers the mail they
    # sent rather than a template that happens to be next in a sequence.
    quoted = (reply_text or "").strip().splitlines()
    quoted = next((q.strip() for q in quoted if len(q.strip()) > 15), "")
    if quoted and intent not in ("NOT_INTERESTED", "AUTO_REPLY"):
        lines += [f"You mentioned: \"{quoted[:160]}\"", ""]

    if intent == "PRICING" and p:
        lines.append("Our published MRPs, the same across every channel:")
        for k in p.skus:
            lines.append(f"• {SKUS[k].line(p.pack)}")
        lines += ["", "Your buying price depends on volume — tell me roughly what "
                      "you would need each month and I will work it out."]
    elif intent == "CATALOGUE" and p:
        lines.append("The blends most relevant to you:")
        for k in p.skus:
            lines.append(f"• {SKUS[k].line(p.pack)}")
        lines += ["", "Every pack is 100% coffee, no chicory, in a food-grade "
                      "glass jar with a two-year shelf life."]
    elif intent == "SAMPLE":
        lines += ["Could you confirm the delivery address and a contact number, "
                  "and I will send it this week?", ""]
        if p:
            lines.append("I would suggest starting with "
                         + " and ".join(SKUS[k].name for k in p.skus[:2]) + ".")
    elif intent == "MEETING":
        when = mem.get("preferred_contact_time")
        lines.append(f"Would {when} suit you?" if when
                     else "What day and time would suit you?")
    elif intent == "EXISTING_SUPPLIER":
        sup = mem.get("current_supplier") or "your current supplier"
        lines += [f"I am not asking you to move away from {sup}. If it is useful, "
                  f"I can send a sample so you can compare on taste and on price, "
                  f"and you decide from there.", ""]
    elif intent == "LATER":
        when = mem.get("next_followup_date") or "then"
        lines += [f"I will come back to you around {when}. If anything changes "
                  f"before that, my number is below.", ""]
    elif intent == "ORDER":
        lines += ["Could you confirm the quantities and pack sizes you need, and "
                  "the delivery address? I will send a proforma the same day.", ""]
    elif intent == "NOT_INTERESTED":
        lines = [greet, "",
                 "Understood — thank you for letting me know, and I will not "
                 "write again.", "",
                 "If your requirements change, my details are below.", ""]
    elif intent in ("QUESTION", "UNCLEAR"):
        lines += ["[Founder: answer their question directly here — the reply is "
                  "quoted above and did not match a standard intent.]", ""]

    if intent not in ("NOT_INTERESTED",):
        lines.append("")
    lines.append(signature)

    subject_map = {
        "PRICING": "Pricing — Purity Beans", "CATALOGUE": "Our range — Purity Beans",
        "SAMPLE": "Sample — Purity Beans", "MEETING": "Meeting — Purity Beans",
        "ORDER": "Your order — Purity Beans",
        "NOT_INTERESTED": "Noted, with thanks",
    }
    return {
        "subject": f"Re: {subject_map.get(intent, 'Purity Beans')} — {lead.company}",
        "body": "\n".join(lines),
        "intent": intent,
        "why_this_draft": (f"{classification.get('label')} — triggered by "
                           f"\"{classification.get('evidence')}\""
                           if classification.get("evidence")
                           else classification.get("label")),
        "confidence": classification.get("confidence"),
        "needs_founder_input": classification.get("needs_founder", False),
        "guidance": classification.get("guidance"),
        "status": "AWAITING_FOUNDER_APPROVAL",
    }
