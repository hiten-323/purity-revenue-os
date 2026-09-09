"""
Contact Trust Engine V2 — promotion, demotion, expiry, revalidation.

The V1 sweep only ever demoted. It re-checked anything claiming to be sendable
and purged what failed, and nothing ever moved an address the other way. The
result was a database that got progressively more restrictive: 136 addresses
read off companies' own websites sat at DISCOVERED forever while the sendable
count stayed at 4. A funnel with no outlet.

This engine behaves like a credit score rather than a pass/fail validator.
Evidence accumulates and lifts a contact; absence of evidence lets it decay.
The critical asymmetry:

    CONCLUSIVE failure  (NXDOMAIN, hard bounce, proven fabrication)
        -> terminal state. PURGED / BOUNCED / INVALID.
    INCONCLUSIVE failure  (greylisting, timeout, network, no evidence yet)
        -> costs confidence, or steps DOWN one level. Never terminal.

That distinction is the whole design. V1 collapsed the two and purged good
addresses on a bad network day — including three we had already successfully
delivered to.

Nothing here overwrites history. Every transition appends a TRUST_TRANSITION
event carrying from/to, reason, evidence, source and confidence, so the path
DISCOVERED -> VALIDATED -> VERIFIED -> ACTIVE -> EXPIRED -> TRUSTED stays
readable years later.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ── States ────────────────────────────────────────────────────────────────
UNSEEN, DISCOVERED, VALIDATED = "UNSEEN", "DISCOVERED", "VALIDATED"
VERIFIED, TRUSTED, ACTIVE = "VERIFIED", "TRUSTED", "ACTIVE"
BOUNCED, INVALID, PURGED, EXPIRED = "BOUNCED", "INVALID", "PURGED", "EXPIRED"

STATES = (UNSEEN, DISCOVERED, VALIDATED, VERIFIED, TRUSTED, ACTIVE,
          BOUNCED, INVALID, PURGED, EXPIRED)

RANK = {UNSEEN: 0, DISCOVERED: 1, VALIDATED: 2, VERIFIED: 3, TRUSTED: 4, ACTIVE: 5}

# Permissions. These three tuples are the ONLY place sending rights are
# decided — V1 had the same question answered in three modules and they drifted
# until the dashboard advertised addresses the sender refused.
MAY_SEND = (VERIFIED, TRUSTED, ACTIVE)
MAY_DRAFT = (DISCOVERED, VALIDATED, VERIFIED, TRUSTED, ACTIVE)
MAY_DRAFT_ONLY = (DISCOVERED, VALIDATED)   # draft but NOT send
MAY_NEVER_SEND = (UNSEEN, BOUNCED, INVALID, PURGED, EXPIRED)

CONFIDENCE_FLOOR = 40      # trust grants permission; confidence earns it
TERMINAL = (PURGED,)                      # only fabrication is unrecoverable

# Time-to-live per level. Past this, a contact goes EXPIRED and must be
# revalidated before it can be used again. It is NOT destroyed — an address
# nobody has touched in a year is stale, not fake.
TTL_DAYS = {DISCOVERED: 90, VALIDATED: 180, VERIFIED: 365, TRUSTED: 730,
            ACTIVE: 30}      # ACTIVE must stay earned, not decay into a lie

# ── Confidence ────────────────────────────────────────────────────────────
# Separate axis from trust. Trust says what we are ALLOWED to do; confidence
# says how sure we are. A VERIFIED contact at confidence 51 and one at 94 have
# the same permissions and very different risk.
EVIDENCE_WEIGHT = {
    # FOUNDER_CALL is 45, not 30. At 30 it sat below the confidence floor of 40,
    # so an address the founder personally heard the buyer dictate was VERIFIED
    # and still not sendable — the strongest routine evidence in the system
    # losing to a website listing plus a DNS lookup. A human at the business
    # saying "send it here" is worth more than any crawl.
    "WEBSITE": 10, "MX": 10, "SMTP": 15, "FOUNDER_CALL": 45, "EMAIL_REPLY": 40,
    "WHATSAPP_REPLY": 30, "LINKEDIN_REPLY": 20, "BUSINESS_MEMORY": 15,
    "MEETING": 35, "BUSINESS_CARD_OCR": 25, "CATALOGUE": 10,
    "PURCHASE_ORDER": 50, "INVOICE": 50, "GST_DOCUMENT": 30,
    "SAMPLE_REQUEST": 30, "PROPOSAL_ACCEPTED": 45, "ORDER": 50, "REORDER": 50,
    "DELIVERED": 20,
}
PENALTY = {"BOUNCE": -60, "COMPLAINT": -70, "MANUAL_EDIT": -35,
           "WEBSITE_REMOVED": -25, "INACTIVITY": -10, "SMTP_TEMP_FAIL": -5}

# Events that prove a human at the business engaged with us.
REPLY_EVENTS = ("EMAIL_REPLY_RECEIVED", "REPLY_RECEIVED", "WHATSAPP_REPLY")
PROMOTING_EVENTS = ("CONTACT_CAPTURED", "CONTACT_VERIFIED", "FOUNDER_CALL",
                    "MEETING_HELD", "SAMPLE_REQUESTED", "PROPOSAL_ACCEPTED",
                    "ORDER_PLACED") + REPLY_EVENTS


def _now() -> datetime:
    return datetime.utcnow()


def normalise(trust: str | None) -> str:
    """Map the V1 vocabulary onto V2 without losing meaning."""
    t = (trust or "").upper().strip()
    return {"": UNSEEN, "UNKNOWN": UNSEEN, "FOUNDER_VERIFIED": VERIFIED,
            "REPLIED": ACTIVE, "UNTRUSTED": DISCOVERED}.get(t, t if t in STATES
                                                            else DISCOVERED)


def may_send(lead) -> tuple[bool, str]:
    """The single authority on whether an address may be used."""
    t = normalise(getattr(lead, "email_trust", None))
    if not (getattr(lead, "email", "") or "").strip():
        return False, "no address on file"
    if t in MAY_NEVER_SEND:
        return False, f"{t} — may never send" + (
            "; revalidate first" if t == EXPIRED else "")
    if t in MAY_DRAFT_ONLY:
        return False, f"{t} — may draft, not send"
    if t not in MAY_SEND:
        return False, t
    c = getattr(lead, "email_confidence", None)
    if c is not None and int(c) < CONFIDENCE_FLOOR:
        return False, f"{t} but confidence {c} < {CONFIDENCE_FLOOR}"
    return True, t


def may_draft(lead) -> bool:
    """Drafting is cheap and reversible; sending is neither."""
    if not (getattr(lead, "email", "") or "").strip():
        return False
    return normalise(getattr(lead, "email_trust", None)) in MAY_DRAFT


def transition(lead, to: str, reason: str, evidence: list | str, source: str,
               db=None, confidence: int | None = None) -> bool:
    """
    Move a contact and append the immutable record. Returns whether it moved.

    Refuses to leave PURGED: that state means the address was proven
    fabricated, and reviving it is how a fake address gets a second life.
    """
    frm = normalise(getattr(lead, "email_trust", None))
    to = to.upper()
    if to not in STATES:
        raise ValueError(f"unknown trust state {to!r}")
    if frm in TERMINAL and to != PURGED:
        return False
    if frm == to:
        return False

    ev = [evidence] if isinstance(evidence, str) else list(evidence or [])
    lead.email_trust = to
    lead.email_verified = to in MAY_SEND
    if source:
        lead.email_source = source
    if hasattr(lead, "email_trust_at"):
        lead.email_trust_at = _now()
    if confidence is not None and hasattr(lead, "email_confidence"):
        lead.email_confidence = max(0, min(100, int(confidence)))

    if db is not None:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="TRUST_TRANSITION", actor="TRUST_ENGINE",
            channel="trust",
            payload={"from": frm, "to": to, "reason": reason, "evidence": ev,
                     "source": source, "confidence": confidence,
                     "technical": getattr(lead, "_tech", None),
                     "address": lead.email},
            occurred_at=_now()))

        # WorkflowEvent is the system's audit trail; this is the business's own
        # record. Both, deliberately: one answers "what did the engine do
        # tonight", the other answers "what happened to THIS account".
        from app.observability import decision
        from app.services import lead_journal as journal
        # RANK is the ladder this module already uses to decide direction
        # (see :303). Reusing it means the journal cannot disagree with the
        # engine about what counts as a promotion.
        promoted = RANK.get(to, 0) > RANK.get(frm, 0)
        decision("email.trust", to, reason, lead=lead, was=frm,
                 confidence=confidence)
        journal.record(
            lead, db, method=journal.TRUST,
            outcome=journal.PROMOTED if promoted else journal.DEMOTED,
            remark=f"{frm} -> {to}: {reason}"
                   + (f" (confidence {confidence})" if confidence is not None else ""),
            by="trust_promoter")
    return True


def confidence_for(lead, db) -> dict:
    """
    Score 0-100 from what actually happened, not from what the address looks
    like. Recomputed rather than stored so it can never drift from the log.
    """
    from app.models.models import WorkflowEvent
    score, contributed = 0, []
    src = (getattr(lead, "email_source", "") or "").upper()
    if src in EVIDENCE_WEIGHT:
        score += EVIDENCE_WEIGHT[src]
        contributed.append(f"{src} +{EVIDENCE_WEIGHT[src]}")

    evs = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id).all()
    seen = set()
    for e in evs:
        w = None
        if e.event_type in REPLY_EVENTS:
            w = ("EMAIL_REPLY", EVIDENCE_WEIGHT["EMAIL_REPLY"])
        elif e.event_type == "FOUNDER_CALL":
            w = ("FOUNDER_CALL", EVIDENCE_WEIGHT["FOUNDER_CALL"])
        elif e.event_type == "EMAIL_SENT":
            w = ("DELIVERED", EVIDENCE_WEIGHT["DELIVERED"])
        elif e.event_type == "ORDER_PLACED":
            w = ("ORDER", EVIDENCE_WEIGHT["ORDER"])
        elif e.event_type == "SAMPLE_REQUESTED":
            w = ("SAMPLE_REQUEST", EVIDENCE_WEIGHT["SAMPLE_REQUEST"])
        elif e.event_type in ("EMAIL_BOUNCED", "HARD_BOUNCE"):
            score += PENALTY["BOUNCE"]
            contributed.append(f"BOUNCE {PENALTY['BOUNCE']}")
            continue
        elif e.event_type == "OUT_OF_BAND_MODIFICATION":
            score += PENALTY["MANUAL_EDIT"]
            contributed.append(f"MANUAL_EDIT {PENALTY['MANUAL_EDIT']}")
            continue
        if w and w[0] not in seen:          # each KIND of evidence counts once
            seen.add(w[0])
            score += w[1]
            contributed.append(f"{w[0]} +{w[1]}")

    # Technical validation is evidence too. Leaving it out scored every
    # website-published address at 10 and the confidence floor then blocked 45
    # perfectly good contacts — the scorer disagreeing with the state machine
    # that had just promoted them. Weights per spec: syntax 5, MX 10, SMTP 15.
    tech = {}
    for e in evs:
        if e.event_type == "TRUST_TRANSITION" and (e.payload or {}).get("technical"):
            tech = e.payload["technical"]
    if tech.get("syntax_ok"):
        score += 5
        contributed.append("SYNTAX +5")
    if tech.get("mx_ok"):
        score += EVIDENCE_WEIGHT["MX"]
        contributed.append(f"MX +{EVIDENCE_WEIGHT['MX']}")
    if tech.get("smtp_ok"):
        score += EVIDENCE_WEIGHT["SMTP"]
        contributed.append(f"SMTP +{EVIDENCE_WEIGHT['SMTP']}")

    last = _last_activity(lead, evs)
    if last and (_now() - last).days > 365:
        score += PENALTY["INACTIVITY"]
        contributed.append(f"INACTIVITY {PENALTY['INACTIVITY']}")
    return {"confidence": max(0, min(100, score)), "why": contributed,
            "last_activity": last}


def _last_activity(lead, evs) -> datetime | None:
    ts = [e.occurred_at for e in evs if e.occurred_at]
    return max(ts) if ts else None


def _counts(evs) -> dict:
    n = {"deliveries": 0, "replies": 0, "meetings": 0, "proposals": 0}
    for e in evs:
        if e.event_type == "EMAIL_SENT":
            n["deliveries"] += 1
        elif e.event_type in REPLY_EVENTS:
            n["replies"] += 1
        elif e.event_type == "MEETING_HELD":
            n["meetings"] += 1
        elif e.event_type == "PROPOSAL_ACCEPTED":
            n["proposals"] += 1
    return n


def evaluate(lead, db, verify=True) -> dict:
    """
    Decide where ONE contact belongs right now. Pure decision + transition;
    the caller commits. This is the whole state machine in one place so the
    rules cannot disagree with each other.
    """
    from app.models.models import WorkflowEvent

    addr = (getattr(lead, "email", "") or "").strip()
    frm = normalise(getattr(lead, "email_trust", None))
    if not addr:
        return {"moved": False, "state": UNSEEN, "reason": "no address"}
    if frm in TERMINAL:
        return {"moved": False, "state": frm, "reason": "terminal"}

    evs = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id).all()
    n = _counts(evs)
    conf = confidence_for(lead, db)
    score, last = conf["confidence"], conf["last_activity"]
    src = (getattr(lead, "email_source", "") or "").upper()

    # ── conclusive negatives first — these are the only terminal moves ─────
    for e in evs:
        if e.event_type in ("EMAIL_BOUNCED", "HARD_BOUNCE"):
            code = str((e.payload or {}).get("code", ""))
            if code.startswith("5") or "unknown user" in str(e.payload).lower():
                if transition(lead, BOUNCED, f"hard bounce {code}".strip(),
                              ["SMTP hard bounce"], src, db, score):
                    return {"moved": True, "state": BOUNCED,
                            "reason": "hard bounce"}
        if e.event_type == "NXDOMAIN":
            if transition(lead, PURGED, "domain does not exist",
                          ["NXDOMAIN"], src, db, 0):
                return {"moved": True, "state": PURGED, "reason": "NXDOMAIN"}

    # ── upward: evidence promotes ─────────────────────────────────────────
    if n["replies"] >= 1 or any(e.event_type in REPLY_EVENTS for e in evs):
        # The buyer answered. Nothing a verifier says outranks that.
        target = ACTIVE if (last and (_now() - last).days <= 30) else TRUSTED
        if RANK.get(target, 0) > RANK.get(frm, 0):
            transition(lead, target, "the business replied to us",
                       ["reply received"], "EMAIL_REPLY", db, score)
            return {"moved": True, "state": target, "reason": "replied"}

    if n["deliveries"] >= 3 or n["meetings"] >= 1 or n["proposals"] >= 1:
        if RANK.get(frm, 0) >= RANK[VERIFIED] and frm != TRUSTED:
            transition(lead, TRUSTED, "repeated successful interaction",
                       [f"{n['deliveries']} deliveries, {n['meetings']} meetings"],
                       src, db, score)
            return {"moved": True, "state": TRUSTED, "reason": "multiple wins"}

    if frm in (UNSEEN, DISCOVERED, VALIDATED, EXPIRED):
        # Business evidence -> VERIFIED. The business publishing the address on
        # its own site IS ownership evidence; that is what "verified" means
        # here, not that a verifier liked the spelling.
        if _is_shared_inbox(lead, db):
            # A real address, so it validates — but one inbox reaching 26 More
            # Supermarket branches is ONE relationship, not 26 send permissions.
            # Capped at draft-only until a human at a specific branch replies,
            # which is the only thing that proves the branch is reachable here.
            if frm != VALIDATED:
                transition(lead, VALIDATED, "shared/chain inbox — one "
                           "relationship, not one per branch",
                           ["address appears on multiple businesses"], src, db,
                           score)
                return {"moved": True, "state": VALIDATED, "reason": "chain inbox"}
            return {"moved": False, "state": VALIDATED, "reason": "chain inbox"}

        if src in ("WEBSITE", "FOUNDER_CALL", "EMAIL_REPLY", "DELIVERED",
                   "BUSINESS_CARD_OCR", "BUSINESS_MEMORY"):
            ok, why = (True, f"published/confirmed via {src.lower()}")
            if verify:
                ok, why = _technically_ok(lead)
            if ok:
                transition(lead, VERIFIED, f"business evidence: {src.lower()}",
                           [src, why], src, db, score)
                return {"moved": True, "state": VERIFIED, "reason": src}
            # inconclusive -> do not fall through to a terminal state
            if frm == UNSEEN:
                transition(lead, DISCOVERED, why, [src], src, db, score)
                return {"moved": True, "state": DISCOVERED, "reason": why}
        elif frm == UNSEEN:
            transition(lead, DISCOVERED, "address collected, provenance unproven",
                       [src or "unknown source"], src, db, score)
            return {"moved": True, "state": DISCOVERED, "reason": "collected"}
        elif frm == DISCOVERED and verify:
            ok, why = _technically_ok(lead)
            if ok:
                transition(lead, VALIDATED, "passed technical validation",
                           [why], src, db, score)
                return {"moved": True, "state": VALIDATED, "reason": why}

    # ── downward: decay, never destruction ────────────────────────────────
    ttl = TTL_DAYS.get(frm)
    if ttl and last and (_now() - last).days > ttl and frm not in (EXPIRED,):
        transition(lead, EXPIRED,
                   f"no activity in {(_now()-last).days} days (TTL {ttl})",
                   ["inactivity"], src, db, score)
        return {"moved": True, "state": EXPIRED, "reason": "ttl"}

    return {"moved": False, "state": frm, "reason": "no change",
            "confidence": score}


def _technically_ok(lead) -> tuple[bool, str]:
    """
    Syntax + MX + SMTP. Returns (conclusive_pass, why).

    A timeout or greylist returns False with a reason that says so — the
    caller must treat that as 'unknown', never as 'bad'. This is the exact
    seam where V1 lost good addresses.
    """
    from app.services.email_verifier import verify_email
    try:
        v = verify_email(lead.email, lead.company or "",
                         getattr(lead, "division", "") or "", lead.website or "")
    except Exception as e:
        return False, f"verifier unavailable ({e.__class__.__name__}) — inconclusive"
    st = (v.get("status") or "UNVERIFIED").upper()
    # stash for the transition record so confidence can see what was checked
    lead._tech = {"syntax_ok": st != "INVALID", "mx_ok": bool(v.get("mx_valid")),
                  "smtp_ok": st in ("VALID", "CATCH_ALL"), "status": st}
    if v.get("is_disposable"):
        return False, "disposable domain"
    if not v.get("mx_valid"):
        return False, "no MX record"
    if st == "INVALID":
        return False, "mailbox rejected"
    if st == "UNVERIFIED":
        return False, "inconclusive — server would not confirm"
    return True, f"{st} confidence {v.get('confidence')}"


def run(db, limit: int = 0, verify: bool = True) -> dict:
    """
    Nightly / post-enrichment / post-reply / post-delivery entry point.
    Safe to run repeatedly: it is a fixpoint, not an accumulator.
    """
    from app.models.models import B2BLead
    rows = db.query(B2BLead).filter(B2BLead.email != "",
                                    B2BLead.email.isnot(None)).all()
    if limit:
        rows = rows[:limit]
    moved, by_state, errors = 0, {}, []
    for l in rows:
        try:
            r = evaluate(l, db, verify=verify)
        except Exception as e:
            errors.append(f"{l.company}: {e.__class__.__name__}: {e}")
            continue
        if r.get("moved"):
            moved += 1
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    db.commit()
    return {"considered": len(rows), "moved": moved, "into": by_state,
            "errors": errors}


def timeline(lead, db) -> list[dict]:
    """Every trust transition for one contact, oldest first. Never rewritten."""
    from app.models.models import WorkflowEvent
    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id == lead.id,
        WorkflowEvent.event_type.in_(
            ["TRUST_TRANSITION", "CONTACT_TRUST_SET", "CONTACT_PURGED",
             "CONTACT_DISCOVERED", "OUT_OF_BAND_MODIFICATION"])
    ).order_by(WorkflowEvent.occurred_at.asc()).all()
    from app.services.timeutil import ist_str
    out = []
    for e in evs:
        p = e.payload or {}
        out.append({"at": ist_str(e.occurred_at), "event": e.event_type,
                    "from": p.get("from"), "to": p.get("to") or p.get("trust"),
                    "reason": p.get("reason") or p.get("note"),
                    "evidence": p.get("evidence"), "source": p.get("source"),
                    "confidence": p.get("confidence")})
    return out


def card(lead, db) -> dict:
    """
    What the founder dashboard shows for one contact: level, confidence, why,
    evidence, last verified, history, risk, next action.
    """
    from app.services.timeutil import ist_str
    t = normalise(getattr(lead, "email_trust", None))
    c = confidence_for(lead, db)
    ok, why = may_send(lead)
    hist = timeline(lead, db)
    if t == EXPIRED:
        nxt = "revalidate before sending"
    elif ok:
        nxt = "safe to send"
    elif t in MAY_DRAFT_ONLY:
        nxt = "draft only — needs business evidence to send"
    else:
        nxt = "do not contact on this address"
    risk = ("high" if c["confidence"] < 40 else
            "medium" if c["confidence"] < 70 else "low")
    return {"address": lead.email, "trust": t, "confidence": c["confidence"],
            "why": c["why"], "evidence": [h.get("evidence") for h in hist if h.get("evidence")],
            "last_verified": ist_str(c["last_activity"]) if c["last_activity"] else None,
            "history": hist, "risk": risk, "next_action": nxt,
            "may_send": ok, "permission": why}


FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
             "rediffmail.com", "yahoo.co.in"}
ROLE_PREFIX = {"info", "support", "contact", "admin", "hello", "sales",
               "care", "enquiry", "enquiries", "feedback", "suggestion",
               "bookings", "booking", "reservations", "reservation", "events"}


def _is_shared_inbox(lead, db) -> bool:
    """
    Does this exact address sit on another business too, or is it a role
    address on free mail?

    Both are the same failure in different clothing: the address does not
    identify THIS business. support@gmail.com belongs to nobody and verifies
    perfectly; hello@more.in belongs to a chain, not to the Ludhiana branch.
    An earlier version of this check took its arguments in the wrong order,
    threw on every call, and had its exception swallowed — so it silently
    approved all 26. Hence: no try/except here, and the count is a plain query.
    """
    from app.models.models import B2BLead
    addr = (lead.email or "").strip().lower()
    if not addr:
        return False
    lp, _, dom = addr.partition("@")
    if dom in FREE_MAIL and lp in ROLE_PREFIX:
        return True
    return db.query(B2BLead).filter(
        B2BLead.email == lead.email, B2BLead.id != lead.id).count() > 0


# ── Event-driven entry points ─────────────────────────────────────────────
# The CRM must never need a manual DB edit. Every real-world occurrence that
# carries evidence has a function here, and each one is the ONLY way that kind
# of evidence enters the system.

def on_reply(lead, db, reply_text: str = "", intent: str = "neutral") -> str:
    """
    A reply is the strongest routine evidence there is: a human at the business
    typed to us. It outranks any verifier verdict, including one that had
    previously demoted the address.
    """
    if intent == "unsubscribe":
        transition(lead, EXPIRED, "unsubscribe received", [reply_text[:200]],
                   "EMAIL_REPLY", db)
        set_confidence(lead, db)
        return EXPIRED
    frm = normalise(getattr(lead, "email_trust", None))
    target = ACTIVE if frm in (VERIFIED, TRUSTED, ACTIVE) else VERIFIED
    transition(lead, target, f"the business replied ({intent})",
               [reply_text[:200]], "EMAIL_REPLY", db)
    set_confidence(lead, db)
    return target


def on_founder_call(lead, db, confirmed: bool, notes: str = "") -> str:
    """The founder heard it from the buyer. Nothing beats that except an order."""
    if not confirmed:
        return normalise(getattr(lead, "email_trust", None))
    transition(lead, VERIFIED, "founder confirmed during call", [notes[:300]],
               "FOUNDER_CALL", db)
    set_confidence(lead, db)
    return VERIFIED


def on_delivery(lead, db, delivered: bool = True, code: str = "") -> str:
    """
    Delivery accepted is positive evidence; a HARD bounce is conclusive and a
    soft one is not. Conflating those is what purged three working addresses.
    """
    if not delivered:
        if code.startswith("5"):
            transition(lead, BOUNCED, f"hard bounce {code}", [code], "SMTP", db, 0)
            set_confidence(lead, db)
            return BOUNCED
        return soft_demote(lead, db, f"soft failure {code} — not conclusive")
    frm = normalise(getattr(lead, "email_trust", None))
    if frm == VALIDATED:
        transition(lead, VERIFIED, "SMTP accepted the message", ["delivered"],
                   "DELIVERED", db)
    set_confidence(lead, db)
    return normalise(getattr(lead, "email_trust", None))


def on_business_event(lead, db, kind: str, detail: dict | None = None) -> str:
    """meeting | proposal | sample_request | order | reorder"""
    target = ACTIVE if kind in ("order", "reorder") else TRUSTED
    transition(lead, target, f"business event: {kind}", [str(detail or kind)],
               kind.upper(), db)
    set_confidence(lead, db)
    return target


def soft_demote(lead, db, reason: str) -> str:
    """
    One rung down, never to a terminal state. A greylist, a timeout, a website
    that changed its contact page — none of those prove the address is fake.
    """
    step = {TRUSTED: VERIFIED, ACTIVE: VERIFIED, VERIFIED: VALIDATED,
            VALIDATED: DISCOVERED, DISCOVERED: DISCOVERED}
    frm = normalise(getattr(lead, "email_trust", None))
    to = step.get(frm, frm)
    if to != frm:
        transition(lead, to, reason, ["soft/inconclusive signal"],
                   getattr(lead, "email_source", "") or "", db)
    set_confidence(lead, db)
    return to


def revalidate(lead, db) -> str:
    """EXPIRED contacts re-enter the ladder rather than being thrown away."""
    if normalise(getattr(lead, "email_trust", None)) != EXPIRED:
        return normalise(getattr(lead, "email_trust", None))
    transition(lead, DISCOVERED, "revalidation started", [], 
               getattr(lead, "email_source", "") or "", db)
    return evaluate(lead, db).get("state", DISCOVERED)


def set_confidence(lead, db) -> int:
    c = confidence_for(lead, db)["confidence"]
    if hasattr(lead, "email_confidence"):
        lead.email_confidence = c
    return c


def replied_businesses(db, limit: int = 50) -> list[dict]:
    """
    Every business that has answered us, newest first — for the founder's
    action list. A reply is the only event in this system that is worth
    interrupting the founder's day for, so it gets its own surface rather than
    being buried among discovery counts.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.timeutil import ist_str
    evs = db.query(WorkflowEvent).filter(
        WorkflowEvent.event_type.in_(list(REPLY_EVENTS))
    ).order_by(WorkflowEvent.occurred_at.desc()).limit(limit).all()
    out, seen = [], set()
    for e in evs:
        if e.lead_id in seen:
            continue
        seen.add(e.lead_id)
        l = db.query(B2BLead).filter(B2BLead.id == e.lead_id).first()
        if not l:
            continue
        p = e.payload or {}
        age = (_now() - e.occurred_at).days if e.occurred_at else None
        out.append({
            "lead_id": l.id, "company": l.company, "city": l.city,
            "category": l.division, "email": l.email,
            "trust": normalise(l.email_trust),
            "confidence": getattr(l, "email_confidence", None),
            "replied_at": ist_str(e.occurred_at) if e.occurred_at else None,
            "days_waiting": age,
            "intent": p.get("intent"), "snippet": (p.get("body") or p.get("text") or "")[:220],
            "urgency": "overdue" if (age or 0) >= 2 else "today",
            "next_action": "read the reply and approve the drafted response",
        })
    return out
