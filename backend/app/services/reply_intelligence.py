"""
Reply Intelligence & Next Best Action.

The dangerous failure here is not misreading a sales objection — it is
mistaking a machine for a person. An out-of-office auto-reply that reads as a
human reply does two wrong things at once: it exits the sequence, so a live
prospect goes silent forever, and it lifts the account frequency cap, so the
chain protection opens up. One misclassified vacation notice can quietly kill
an opportunity and unlock 26 branches at the same time.

So machine detection runs FIRST, is header-driven, and is biased toward
calling things machines. A human misread as a machine costs one continued
follow-up. A machine misread as a human costs the opportunity.

Nothing here sends. Every commercial action lands in the approval queue with
its reasoning attached, because the actions this engine proposes — pricing,
samples, delivery dates — are exactly the ones that must never be automatic.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# ── Level 1: sender classification ────────────────────────────────────────
HUMAN = "HUMAN"
MACHINE_KINDS = ("OUT_OF_OFFICE", "AUTO_RESPONDER", "DELIVERY_RECEIPT",
                 "READ_RECEIPT", "BOUNCE", "SECURITY_GATEWAY", "SPAM_FILTER",
                 "NEWSLETTER")

# Headers are the reliable signal; body text is the fallback. RFC 3834
# (Auto-Submitted) and Microsoft/Google's X-Auto-Response-Suppress are set by
# the responding server, not by whoever wrote the message.
_AUTO_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond",
                 "x-auto-response-suppress", "precedence: auto_reply",
                 "precedence: bulk", "precedence: junk", "x-mailer: vacation")

_OOO = re.compile(
    r"\b(out of (the )?office|on (annual |maternity |paternity )?leave|"
    r"away from (my |the )?(desk|office)|on vacation|on holiday|"
    r"currently unavailable|will (be )?(back|return)( to the office)?( on)?|"
    r"returning on|auto[- ]?reply|automatic reply|autoreply)\b", re.I)
_DELIVERY = re.compile(
    r"\b(delivery status notification|delivery receipt|message delivered|"
    r"successfully delivered|read receipt|has been read|return receipt)\b", re.I)
_BOUNCE = re.compile(
    r"\b(undeliverable|delivery has failed|could not be delivered|"
    r"mailbox (is )?(full|unavailable)|user unknown|recipient (address )?rejected|"
    r"550|551|553|permanent (error|failure))\b", re.I)
_GATEWAY = re.compile(
    r"\b(mimecast|proofpoint|barracuda|spam ?(filter|quarantine)|"
    r"message quarantined|scanned by|virus scan|click here to release)\b", re.I)
_NEWSLETTER = re.compile(
    r"\b(unsubscribe from this list|view (this|in) browser|"
    r"you (are )?receiv(e|ing) this (email|newsletter) because)\b", re.I)

_RETURN_DATE = re.compile(
    r"\b(?:back|return(?:ing)?|available|until|till|upto|up to)\b[^.\n]{0,30}?"
    r"(\d{1,2}[\s/-](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:[\s/-]\d{2,4})?|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", re.I)


def classify_sender(subject: str = "", body: str = "",
                    headers: dict | None = None) -> dict:
    """Human or machine, and which kind. Header evidence outranks body text."""
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    blob = f"{subject}\n{body}"
    raw_hdr = " ".join(f"{k}: {v}" for k, v in h.items())

    # Empty envelope sender is the classic machine marker.
    if h.get("return-path", "").strip() in ("<>", ""):
        if h.get("return-path") is not None and h.get("return-path") == "<>":
            return {"sender": "MACHINE", "kind": "BOUNCE",
                    "why": "empty Return-Path — automated bounce envelope",
                    "confidence": 99}
    if _BOUNCE.search(blob):
        return {"sender": "MACHINE", "kind": "BOUNCE",
                "why": "bounce language in the message", "confidence": 95}
    if any(k in raw_hdr for k in _AUTO_HEADERS):
        kind = "OUT_OF_OFFICE" if _OOO.search(blob) else "AUTO_RESPONDER"
        return {"sender": "MACHINE", "kind": kind,
                "why": "auto-submitted header set by the responding server",
                "confidence": 98}
    if _OOO.search(blob):
        return {"sender": "MACHINE", "kind": "OUT_OF_OFFICE",
                "why": "out-of-office language", "confidence": 90}
    if _DELIVERY.search(blob):
        return {"sender": "MACHINE", "kind": "DELIVERY_RECEIPT",
                "why": "delivery/read receipt", "confidence": 95}
    if _GATEWAY.search(blob):
        return {"sender": "MACHINE", "kind": "SECURITY_GATEWAY",
                "why": "security gateway or quarantine notice", "confidence": 92}
    if _NEWSLETTER.search(blob):
        return {"sender": "MACHINE", "kind": "NEWSLETTER",
                "why": "bulk newsletter markers", "confidence": 85}
    return {"sender": HUMAN, "kind": HUMAN,
            "why": "no machine markers found", "confidence": 75}


def return_date(body: str) -> str | None:
    m = _RETURN_DATE.search(body or "")
    return m.group(1) if m else None


# ── Level 2: commercial intent (multi-label) ──────────────────────────────
INTENTS = {
    # positive
    "PRICING_REQUEST":   (r"\b(price|pricing|rate list|rates|quotation|quote|"
                          r"cost|price list|per (kg|jar|unit)|margin sheet)\b", "positive"),
    "CATALOGUE_REQUEST": (r"\b(catalogue|catalog|product list|brochure|"
                          r"product range|share (your )?products)\b", "positive"),
    "SAMPLE_REQUEST":    (r"\b(sample|samples|trial (pack|order)|test the|"
                          r"try (it|your)|send.{0,12}sample)\b", "positive"),
    "MEETING_REQUEST":   (r"\b(meet|meeting|visit|come (over|by)|appointment|"
                          r"schedule a|demo)\b", "positive"),
    "CALL_ME":           (r"\b(call me|give me a call|ring me|phone me|"
                          r"contact me on|reach me at|whatsapp me)\b", "positive"),
    "INTERESTED":        (r"\b(interested|keen|sounds good|looks good|"
                          r"we would like|happy to|please share|do share)\b", "positive"),
    "PROCUREMENT_REDIRECT": (r"\b(procurement|purchase (dept|department|team|manager)|"
                          r"buying (team|dept)|contact.{0,20}(purchase|procurement)|"
                          r"forward(ed|ing)? (this )?to)\b", "neutral"),
    "DISTRIBUTOR_ENQUIRY": (r"\b(distributor|distributorship|dealership|"
                          r"stockist|franchise|agency)\b", "positive"),
    "VENDOR_REGISTRATION": (r"\b(vendor (registration|form|code|onboarding)|"
                          r"empanel|supplier registration|gst certificate|msme)\b", "positive"),
    # neutral
    "CALL_LATER":        (r"\b(next week|next month|later|after (diwali|holi|"
                          r"the season)|busy (right )?now|call.{0,10}(later|"
                          r"next)|get back to you)\b", "neutral"),
    "WRONG_CONTACT":     (r"\b(wrong (person|contact|number|address)|not the right|"
                          r"no longer (with|works)|i (do not|don't) handle|"
                          r"not my department)\b", "neutral"),
    "NEED_INFO":         (r"\b(more (info|information|details)|tell me more|"
                          r"what (is|are) your|clarif)\b", "neutral"),
    # negative
    "EXISTING_SUPPLIER": (r"\b(already (buy|buying|have|working with|sourc)|"
                          r"existing (supplier|vendor)|current supplier|"
                          r"we (use|stock|carry)\s+\w+|tied up with|"
                          r"nescafe|bru|continental|levista|tata coffee)\b", "negative"),
    "PRICE_TOO_HIGH":    (r"\b(too (expensive|costly|high)|price is high|"
                          r"cheaper|not competitive|budget (is )?(low|tight))\b", "negative"),
    "NO_REQUIREMENT":    (r"\b(no (requirement|need|demand)|do not (need|require)|"
                          r"don't (need|require)|not (looking|required))\b", "negative"),
    "NOT_INTERESTED":    (r"\b(not interested|no thanks|no thank you|"
                          r"pass on this|decline)\b", "negative"),
    "DO_NOT_CONTACT":    (r"\b(remove me|unsubscribe|stop (emailing|contacting)|"
                          r"do not (contact|email)|don't contact|"
                          r"take me off)\b", "negative"),
    # An explicit request to be contacted on WhatsApp, and nothing weaker.
    #
    # The reply must NAME the channel. A bare "yes" is not matched here even
    # when the email that prompted it asked about WhatsApp, because the
    # evidence row then reads "yes" and proves nothing on its own — which is
    # precisely the argument someone will need it to survive. The outbound ask
    # (email_sender.whatsapp_ask) therefore requests the word itself, so the
    # reply carries its own proof.
    #
    # Deliberately NOT matched: "we are on whatsapp", "our whatsapp is 98...",
    # "whatsapp group", a signature block containing a WhatsApp number. Those
    # state that the channel exists, which is never permission to use it.
    "WHATSAPP_OPT_IN":   (r"(?:\b(?:yes|yeah|sure|ok|okay|please|kindly|pls|do)\b[^.!?\n]{0,20}"
                          r"\bwhats\s?app\b"
                          r"|\bwhats\s?app\b[^.!?\n]{0,20}\b(?:is\s+)?(?:fine|ok|okay|good|better|"
                          r"works|preferred|please|yes)\b"
                          r"|\b(?:send|share|forward|ping|message|msg|share it|send it)\b"
                          r"[^.!?\n]{0,25}\bwhats\s?app\b)", "positive"),
}

# Which intents pause cold outreach. Machine kinds are deliberately absent —
# that omission is the whole point of this module.
PAUSING = ("PRICING_REQUEST", "CATALOGUE_REQUEST", "SAMPLE_REQUEST",
           "MEETING_REQUEST", "CALL_ME", "INTERESTED", "DISTRIBUTOR_ENQUIRY",
           "VENDOR_REGISTRATION", "PROCUREMENT_REDIRECT", "CALL_LATER",
           "NEED_INFO", "WRONG_CONTACT", "EXISTING_SUPPLIER",
           "PRICE_TOO_HIGH", "NO_REQUIREMENT", "NOT_INTERESTED",
           "DO_NOT_CONTACT", "WHATSAPP_OPT_IN")

# Response-time targets. Sorting a reply queue by arrival time treats a sample
# request the same as a vacation notice.
SLA_MINUTES = {"MEETING_REQUEST": 15, "SAMPLE_REQUEST": 30,
               "PRICING_REQUEST": 30, "CALL_ME": 30,
               "PROCUREMENT_REDIRECT": 60, "INTERESTED": 60,
               "DISTRIBUTOR_ENQUIRY": 60, "VENDOR_REGISTRATION": 120,
               "CATALOGUE_REQUEST": 120, "NEED_INFO": 240,
               "WRONG_CONTACT": 1440, "EXISTING_SUPPLIER": 1440,
               "CALL_LATER": 1440, "PRICE_TOO_HIGH": 1440,
               "NO_REQUIREMENT": 2880, "NOT_INTERESTED": 2880,
               "DO_NOT_CONTACT": 60, "WHATSAPP_OPT_IN": 60}

NEXT_ACTION = {
    "PRICING_REQUEST":   ("DRAFT_PRICING", "send the rate card for their category"),
    "CATALOGUE_REQUEST": ("DRAFT_CATALOGUE", "send the product range"),
    "SAMPLE_REQUEST":    ("PREPARE_SAMPLE", "confirm address and prepare dispatch"),
    "MEETING_REQUEST":   ("BOOK_MEETING", "propose two slots and confirm"),
    "CALL_ME":           ("FOUNDER_CALL", "founder calls — highest conversion touch"),
    "INTERESTED":        ("DRAFT_REPLY", "answer and propose a concrete next step"),
    "DISTRIBUTOR_ENQUIRY": ("DRAFT_DISTRIBUTOR_TERMS", "trade margins and territory"),
    "VENDOR_REGISTRATION": ("SUBMIT_VENDOR_DOCS", "GST, FSSAI, bank details"),
    "PROCUREMENT_REDIRECT": ("UPDATE_ACCOUNT_CONTACT", "add procurement and route there"),
    "CALL_LATER":        ("SCHEDULE_FOLLOWUP", "diarise exactly when they asked"),
    "WRONG_CONTACT":     ("FIND_DECISION_MAKER", "discover the right person"),
    "NEED_INFO":         ("DRAFT_REPLY", "answer the specific question asked"),
    "EXISTING_SUPPLIER": ("COMPETITOR_BATTLE_CARD", "position as complement, not replacement"),
    "PRICE_TOO_HIGH":    ("MARGIN_REVIEW", "founder decides on price — never automatic"),
    "NO_REQUIREMENT":    ("NURTURE", "low frequency, seasonal check-in"),
    "NOT_INTERESTED":    ("CLOSE_POLITELY", "close the file, leave the door open"),
    "DO_NOT_CONTACT":    ("SUPPRESS_ACCOUNT", "suppress immediately, whole account"),
    "WHATSAPP_OPT_IN":   ("SEND_WHATSAPP", "explicit opt-in — WhatsApp is now a permitted channel"),
}

# Actions the founder must always take personally.
FOUNDER_ONLY = ("MARGIN_REVIEW", "FOUNDER_CALL", "DRAFT_PRICING",
                "DRAFT_DISTRIBUTOR_TERMS", "PREPARE_SAMPLE")


def classify_intent(text: str) -> dict:
    """Multi-label. 'We buy Nescafe but send your catalogue, call next week'
    is three facts, and collapsing it to one loses two of them."""
    t = text or ""
    hits, sentiments = [], []
    for name, (pat, pol) in INTENTS.items():
        m = re.search(pat, t, re.I)
        if m:
            hits.append({"intent": name, "polarity": pol,
                         "matched": m.group(0)[:40]})
            sentiments.append(pol)
    if not hits:
        return {"intents": [], "polarity": "unknown", "confidence": 30,
                "why": "no commercial intent matched — founder review"}
    pol = ("negative" if "negative" in sentiments else
           "positive" if "positive" in sentiments else "neutral")
    # More independent signals -> more confidence, capped well short of certain.
    conf = min(92, 55 + 12 * len(hits))
    return {"intents": hits, "polarity": pol, "confidence": conf,
            "why": f"{len(hits)} intent signal(s) matched"}


def analyse(subject: str, body: str, headers: dict | None = None) -> dict:
    """The full pipeline for one inbound message."""
    snd = classify_sender(subject, body, headers)
    text = f"{subject}\n{body}"

    if snd["sender"] != HUMAN:
        # A machine never counts as engagement, never pauses the sequence and
        # never lifts the account cap. Only the affected contact is touched.
        pause = False
        act, why = {
            "OUT_OF_OFFICE": ("RESUME_AFTER_RETURN",
                              "hold this contact until they are back"),
            "BOUNCE": ("MARK_BOUNCED", "demote this address only"),
            "DELIVERY_RECEIPT": ("NONE", "delivery confirmed, cadence continues"),
            "READ_RECEIPT": ("NONE", "read receipt, cadence continues"),
            "SECURITY_GATEWAY": ("NONE", "gateway notice, cadence continues"),
            "SPAM_FILTER": ("REVIEW_DELIVERABILITY", "filtered — watch the domain"),
            "NEWSLETTER": ("NONE", "bulk mail, ignore"),
            "AUTO_RESPONDER": ("NONE", "acknowledgement, cadence continues"),
        }.get(snd["kind"], ("NONE", "machine reply"))
        return {"sender": snd, "human": False, "intents": [],
                "polarity": "machine", "confidence": snd["confidence"],
                "pause_sequence": pause, "pause_account": False,
                "counts_as_engagement": False,
                "next_action": act, "next_action_why": why,
                "resume_after": return_date(body) if snd["kind"] == "OUT_OF_OFFICE" else None,
                "sla_minutes": None, "needs_founder": False,
                "audit": [f"sender={snd['kind']} ({snd['why']})",
                          "machine reply: sequence NOT paused, account cap NOT lifted"]}

    it = classify_intent(text)
    names = [h["intent"] for h in it["intents"]]
    pause = any(n in PAUSING for n in names)
    sla = min([SLA_MINUTES[n] for n in names if n in SLA_MINUTES], default=240)

    # Rank the actions: the most commercially urgent intent wins.
    #
    # `ordered` is empty whenever a human writes something this table does not
    # recognise, which is most short replies: "ok", "Thanks, noted.", "Yes
    # please." Indexing it directly raised IndexError, and the caller in
    # endpoints.sync_email_replies catches that as "reply auto-draft failed" —
    # so the reply was stored, the lead was marked REPLIED, and then the
    # intelligence, the trust promotion and the engagement signal that closes
    # the account to cold outreach were all silently skipped. Found by the
    # consent tests below, on deployed production code.
    #
    # An unreadable reply is founder work, not an error: a human said something
    # and nobody can tell what, which is exactly when a person should look.
    ordered = sorted(names, key=lambda n: SLA_MINUTES.get(n, 9999))
    act, why = (
        NEXT_ACTION.get(ordered[0], ("FOUNDER_REVIEW", "unclassified — founder decides"))
        if ordered
        else ("FOUNDER_REVIEW", "a human replied and no intent matched — founder reads it")
    )
    conf = it["confidence"]
    needs_founder = (conf < 70 or act in FOUNDER_ONLY or not names)

    return {"sender": snd, "human": True, "intents": it["intents"],
            "polarity": it["polarity"], "confidence": conf,
            "pause_sequence": pause,
            # A human reply anywhere in the account stops COLD outreach to the
            # rest of it. The live conversation continues.
            "pause_account": pause,
            "counts_as_engagement": True,
            "next_action": act, "next_action_why": why,
            "secondary_actions": [NEXT_ACTION[n][0] for n in ordered[1:]
                                  if n in NEXT_ACTION],
            "sla_minutes": sla, "needs_founder": needs_founder,
            "audit": [f"sender=HUMAN ({snd['why']})",
                      f"intents={names or ['none']}",
                      f"confidence={conf} — {'founder review' if needs_founder else 'auto-actionable'}",
                      f"pause_sequence={pause}, pause_account={pause}",
                      f"SLA {sla} min, action {act}"]}


def facts_from(text: str) -> dict:
    """Commercial entities worth keeping. Only what is literally present."""
    out = {}
    comp = re.findall(r"\b(nescafe|nestl[eé]|bru|continental|levista|tata coffee|"
                      r"third wave|blue tokai|sleepy owl|davidoff|moccona)\b",
                      text or "", re.I)
    if comp:
        out["competitors"] = sorted({c.title() for c in comp})
    vol = re.findall(r"\b(\d{1,5})\s*(kg|kgs|jars?|cartons?|boxes|cases|tonnes?|tons?)\b",
                     text or "", re.I)
    if vol:
        out["volumes"] = [f"{n} {u.lower()}" for n, u in vol]
    money = re.findall(r"(?:rs\.?|inr|₹)\s?([\d,]+)", text or "", re.I)
    if money:
        out["amounts"] = [m.replace(",", "") for m in money]
    phone = re.findall(r"\b((?:\+91[\s-]?)?[6-9]\d{9})\b", text or "")
    if phone:
        out["phones"] = sorted(set(phone))
    return out


def process(lead, db, subject: str, body: str, headers: dict | None = None,
            from_addr: str = "") -> dict:
    """
    Classify, record, and decide — the single entry point for the poller.
    Writes events; commits nothing commercial without founder approval.
    """
    from app.models.models import WorkflowEvent
    from app.services import trust_promoter as tp

    r = analyse(subject, body, headers)
    facts = facts_from(f"{subject}\n{body}") if r["human"] else {}
    now = datetime.utcnow()

    db.add(WorkflowEvent(
        lead_id=lead.id,
        event_type="EMAIL_REPLY_RECEIVED" if r["human"] else "MACHINE_REPLY",
        actor="REPLY_INTELLIGENCE", channel="email",
        payload={"from": from_addr, "subject": subject[:200],
                 "body": (body or "")[:1500],
                 "sender_kind": r["sender"]["kind"],
                 "intents": [i["intent"] for i in r["intents"]],
                 "polarity": r["polarity"], "confidence": r["confidence"],
                 "next_action": r["next_action"], "sla_minutes": r["sla_minutes"],
                 "facts": facts, "audit": r["audit"],
                 "resume_after": r.get("resume_after")},
        occurred_at=now))

    if r["human"] and r["counts_as_engagement"]:
        # A reply is the strongest evidence a contact is real.
        tp.on_reply(lead, db, body or "", r["polarity"])
    elif r["sender"]["kind"] == "BOUNCE":
        tp.on_delivery(lead, db, delivered=False, code="550")

    # An explicit WhatsApp opt-in, from a human, becomes recorded consent —
    # email asking for it is the cleanest automated route to a channel that
    # otherwise has no lawful way to open.
    #
    # Guarded on r["human"] for the reason this whole module exists: an
    # auto-responder whose signature block mentions WhatsApp is not a business
    # agreeing to anything. The words themselves are stored as the evidence,
    # and whatsapp_consent.record refuses to bind consent when there is no
    # number to bind it to.
    if r["human"] and any(i["intent"] == "WHATSAPP_OPT_IN" for i in r["intents"]):
        from app.services import whatsapp_consent

        try:
            whatsapp_consent.record(
                lead, db,
                source="EMAIL_REPLY_WHATSAPP_REQUEST",
                evidence=(body or "").strip(),
                message_id=str((headers or {}).get("Message-ID")
                               or (headers or {}).get("message-id") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            # Never let consent bookkeeping lose the reply itself. A failure
            # here means no consent was recorded, which is the safe direction.
            import logging
            logging.getLogger(__name__).warning(
                "whatsapp consent not recorded for lead %s: %s: %s",
                getattr(lead, "id", None), type(exc).__name__, exc)

    if facts:
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="BUSINESS_FACT_LEARNED",
            actor="REPLY_INTELLIGENCE", channel="memory",
            payload={"facts": facts, "source": f"reply from {from_addr}",
                     "observed_at": now.isoformat()},
            occurred_at=now))
    db.commit()
    return {**r, "facts": facts}
