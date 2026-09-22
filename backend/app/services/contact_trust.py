"""
Contact trust layer.

WHY
82 addresses were written straight into SQLite with email_verified=1 and no
event. This application is therefore not the only writer to its own database,
and "the row says verified" stopped being evidence of anything.

So trust is not a boolean and is not granted by existence. A contact carries a
trust level and a provenance, outreach reads the trust level, and any change the
application did not make is detected and re-checked rather than inherited.

WHAT COUNTS AS PROOF, STRONGEST FIRST
  REPLIED           they sent us mail from it — unarguable
  FOUNDER_VERIFIED  the buyer gave it to the founder on a call
  VERIFIED          passed the verification workflow
  DISCOVERED        seen on a public source — a lead, not a licence to send
  UNKNOWN / UNTRUSTED / PURGED — not sendable

DISCOVERED is deliberately NOT sendable. Discovery may create contacts; only
verification may bless them. Collapsing those two steps is what produced 63
NXDOMAIN bounces.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

SENDABLE = ("VERIFIED", "FOUNDER_VERIFIED", "REPLIED")
ALL_STATES = ("UNKNOWN", "DISCOVERED", "VERIFIED", "FOUNDER_VERIFIED",
              "REPLIED", "UNTRUSTED", "PURGED")


def fingerprint(lead) -> str:
    """Hash of the contact fields as the application last left them."""
    raw = "|".join(str(getattr(lead, f, "") or "") for f in
                   ("email", "phone", "whatsapp_number",
                    "email_trust", "phone_trust", "email_verified"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def stamp(lead) -> None:
    """Record the current contact state as application-authored."""
    lead.contact_fingerprint = fingerprint(lead)


def is_out_of_band(lead) -> bool:
    """
    Did someone other than this application change the contact fields?

    A stored fingerprint that no longer matches means the row moved without the
    application moving it. No fingerprint at all means the row predates the
    trust layer — unknown, not innocent.
    """
    fp = getattr(lead, "contact_fingerprint", None)
    return bool(fp) and fp != fingerprint(lead)


def sendable(lead) -> tuple[bool, str]:
    """
    Delegates to the V2 engine. This function stays because ~10 modules import
    it, but it must not hold a SECOND opinion — two modules answering "is this
    sendable?" separately is what put "3 email ready" on the dashboard while
    the sender refused all three.
    """
    from app.services.trust_promoter import may_send
    return may_send(lead)


SENDABLE = ("VERIFIED", "TRUSTED", "ACTIVE", "FOUNDER_VERIFIED", "REPLIED")

def grant(lead, trust: str, source: str, db=None, note: str = "") -> None:
    """
    Raise a contact to a trust level, with provenance, and log it. Every trust
    change leaves an event — that is what makes a later silent change visible.
    """
    trust = trust.upper()
    if trust not in ALL_STATES:
        raise ValueError(f"unknown trust state {trust}")
    now = datetime.utcnow()
    lead.email_trust = trust
    lead.email_source = source
    if lead.email_collected_at is None:
        lead.email_collected_at = now
    if trust in SENDABLE:
        lead.email_verified_at = now
        lead.email_verified = True
    else:
        lead.email_verified = False
    stamp(lead)
    if db is not None:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="CONTACT_TRUST_SET", actor="SYSTEM",
            channel="trust",
            payload={"trust": trust, "source": source, "note": note,
                     "address": lead.email},
            occurred_at=now))


def audit(db) -> dict:
    """
    Can the CRM be trusted? Counts only — every number is a count of rows.
    """
    from app.models.models import B2BLead
    leads = db.query(B2BLead).all()
    by_trust: dict[str, int] = {}
    oob = 0
    for l in leads:
        t = (getattr(l, "email_trust", "") or "UNKNOWN").upper()
        if (l.email or "").strip():
            by_trust[t] = by_trust.get(t, 0) + 1
        if is_out_of_band(l):
            oob += 1
    with_addr = sum(1 for l in leads if (l.email or "").strip())
    return {
        "leads": len(leads),
        "with_address": with_addr,
        "by_trust": dict(sorted(by_trust.items(), key=lambda kv: -kv[1])),
        "sendable": sum(v for k, v in by_trust.items() if k in SENDABLE),
        "out_of_band_modifications": oob,
        "note": ("DISCOVERED contacts are intentionally not sendable — "
                 "discovery finds addresses, verification blesses them"),
    }


def sweep(db, reverify=True) -> dict:
    """
    The continuous integrity check. Safe to run at boot, before a batch
    approval, before a send, and on a schedule.

    Anything claiming a sendable trust level is re-checked against the verifier;
    anything that fails is demoted to PURGED with an event. Founder-verified and
    replied contacts are exempt — the buyer's own word beats a DNS lookup.
    Inconclusive lookups change nothing, so a network blip cannot purge a good
    address.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.email_verifier import verify_email

    demoted, flagged = 0, 0
    for l in db.query(B2BLead).all():
        # Reconcile the legacy boolean with the trust level FIRST. Something
        # outside this application keeps writing email_verified=1 with no trust
        # level and no source — 16 rows appeared that way, two of them carrying
        # jane.doe@ and jsmith@. sendable() already refuses them because the
        # trust level is UNKNOWN, but leaving the boolean set means any code
        # still reading it sees a verified address. The trust level is the
        # authority; the boolean must follow it, never lead.
        _t = (getattr(l, "email_trust", "") or "UNKNOWN").upper()
        if bool(getattr(l, "email_verified", False)) and _t not in SENDABLE:
            l.email_verified = False
            if _t == "UNKNOWN" and (l.email or "").strip():
                l.email_trust = "UNTRUSTED"      # written by someone else
                flagged += 1
                db.add(WorkflowEvent(
                    lead_id=l.id, event_type="OUT_OF_BAND_MODIFICATION",
                    actor="SYSTEM", channel="trust",
                    payload={"address": l.email,
                             "note": "email_verified=1 with no trust level and no "
                                     "source — written outside the application"},
                    occurred_at=datetime.utcnow()))
        addr = (l.email or "").strip()
        if not addr:
            continue
        trust = (getattr(l, "email_trust", "") or "UNKNOWN").upper()

        if is_out_of_band(l):
            flagged += 1
            # Emit at most one OOB event per mismatch cycle, then re-stamp so
            # every API boot does not stack MANUAL_EDIT -35 forever.
            prior = db.query(WorkflowEvent).filter(
                WorkflowEvent.lead_id == l.id,
                WorkflowEvent.event_type == "OUT_OF_BAND_MODIFICATION",
            ).count()
            if prior == 0:
                db.add(WorkflowEvent(
                    lead_id=l.id, event_type="OUT_OF_BAND_MODIFICATION",
                    actor="SYSTEM", channel="trust",
                    payload={"address": addr, "claimed_trust": trust,
                             "note": "contact fields changed with no application event"},
                    occurred_at=datetime.utcnow()))
            if trust in SENDABLE:
                trust = "UNTRUSTED"
                l.email_trust = "UNTRUSTED"
            stamp(l)

        # An address we have SUCCESSFULLY DELIVERED to is proven by the delivery
        # itself. The sweep was purging exactly those: admin@bgtechvista.com was
        # emailed successfully, then deleted on the next boot because the
        # verifier dislikes a generic "admin" prefix. A heuristic about what an
        # address looks like cannot outrank an SMTP server accepting it.
        _delivered = db.query(WorkflowEvent).filter(
            WorkflowEvent.lead_id == l.id,
            WorkflowEvent.event_type == "EMAIL_SENT").first() is not None
        if _delivered and trust not in ("PURGED",):
            if trust not in SENDABLE:
                l.email_trust = "VERIFIED"
                l.email_verified = True
                l.email_source = l.email_source or "DELIVERED"
            stamp(l)
            continue

        if not reverify or trust in ("FOUNDER_VERIFIED", "REPLIED", "PURGED"):
            stamp(l)
            continue

        if trust == "VERIFIED" or bool(getattr(l, "email_verified", False)):
            try:
                r = verify_email(addr, l.company or "", l.website or "")
            except Exception:
                stamp(l)
                continue                      # inconclusive — leave it alone
            if r.get("status") not in ("VALID", "CATCH_ALL"):
                l.email = ""
                l.email_trust = "PURGED"
                l.email_verified = False
                db.add(WorkflowEvent(
                    lead_id=l.id, event_type="CONTACT_PURGED", actor="SYSTEM",
                    channel="trust",
                    payload={"removed_email": addr,
                             "reason": f"claimed {trust}, failed live "
                                       f"verification: {r.get('reason')}"},
                    occurred_at=datetime.utcnow()))
                demoted += 1
        stamp(l)

    db.commit()
    return {"demoted_to_purged": demoted, "out_of_band_flagged": flagged}


# ── Actionability, independent of trust ──────────────────────────────────────
# "Can this contact be trusted?" and "should we contact them today?" are
# different questions. A REPLIED address is maximally trustworthy and may still
# be OPTED_OUT. Keeping them in one field would make an opt-out indistinguishable
# from a data-quality failure.

CONTACT_STATUSES = ("CONTACTABLE", "FOLLOW_UP", "PAUSED", "DO_NOT_CONTACT",
                    "OPTED_OUT", "BOUNCED", "ARCHIVED")
ACTIONABLE = ("CONTACTABLE", "FOLLOW_UP")

BUSINESS_TRUST = ("UNVERIFIED", "VERIFIED_EXISTING", "PERMANENTLY_CLOSED",
                  "NOT_A_BUSINESS")

# Evidence points. Deliberately additive and capped: a reply alone is decisive,
# but nothing else on its own should read as certainty.
_EVIDENCE = (
    ("website live",        20, lambda l: bool((l.website or "").strip())),
    ("MX / domain active",  15, lambda l: (l.email_trust or "") in
                                ("VERIFIED", "FOUNDER_VERIFIED", "REPLIED")),
    ("business verified",   20, lambda l: (l.business_trust or "") == "VERIFIED_EXISTING"),
    ("founder confirmed",   35, lambda l: (l.email_trust or "") == "FOUNDER_VERIFIED"),
    ("inbound reply",      100, lambda l: (l.email_trust or "") == "REPLIED"),
)


def confidence(lead) -> dict:
    """0-100 from evidence actually on the record, with the reasons."""
    score, why = 0, []
    for label, pts, test in _EVIDENCE:
        try:
            if test(lead):
                score += pts
                why.append(f"{label} (+{pts})")
        except Exception:
            continue
    return {"score": min(100, score), "evidence": why}


def actionable(lead) -> tuple[bool, str]:
    """
    Should we contact this business today? Trust is checked separately by
    sendable(); this is purely the commercial decision.
    """
    st = (getattr(lead, "contact_status", "") or "CONTACTABLE").upper()
    if st in ACTIONABLE:
        return True, st
    return False, (getattr(lead, "contact_status_reason", None)
                   or f"status is {st}")


def set_status(lead, status: str, reason: str = "", db=None) -> None:
    """Change the commercial status. Never touches trust."""
    status = status.upper()
    if status not in CONTACT_STATUSES:
        raise ValueError(f"unknown contact_status {status}")
    lead.contact_status = status
    lead.contact_status_reason = reason or None
    if db is not None:
        from app.models.models import WorkflowEvent
        db.add(WorkflowEvent(
            lead_id=lead.id, event_type="CONTACT_STATUS_SET", actor="SYSTEM",
            channel="trust", payload={"status": status, "reason": reason},
            occurred_at=datetime.utcnow()))


def acquisition_queue(db, limit: int = 100) -> dict:
    """
    Businesses that qualify but cannot yet be reached — the work that has to
    happen before outreach is even possible.

    Reporting "0 sendable" describes a dead end. The same 151 businesses are
    really a queue: each one needs a contact found, and the route depends on
    what is already known. This turns the gap into the day's task list.
    """
    from app.models.models import B2BLead

    rows, by_route = [], {}
    for l in db.query(B2BLead).all():
        if (l.status or "") in ("DISQUALIFIED", "CLOSED_LOST", "ORDER_WON"):
            continue
        ok_status, _ = actionable(l)
        if not ok_status:
            continue
        can_email, _ = sendable(l)
        has_phone = bool((l.phone or "").strip() or (l.whatsapp_number or "").strip())
        if can_email:
            continue                      # already reachable by email

        # Route by the cheapest evidence we already hold.
        if has_phone:
            route, why = "FOUNDER_CALL", "phone on record — ask for the email on the call"
        elif (l.website or "").strip():
            route, why = "WEBSITE", "website known — read the contact page"
        elif (l.place_id or "").strip():
            route, why = "GOOGLE_MAPS", "Maps listing exists — check the profile for contacts"
        else:
            route, why = "DIRECTORY_SEARCH", "no contact and no website — IndiaMART / TradeIndia / LinkedIn"

        by_route[route] = by_route.get(route, 0) + 1
        rows.append({
            "lead_id": l.id, "company": l.company, "city": l.city,
            "category": l.division, "route": route, "why": why,
            "has_phone": has_phone,
            "website": (l.website or "") or None,
            "business_trust": l.business_trust,
            "confidence": confidence(l)["score"],
        })

    rows.sort(key=lambda r: (-int(r["has_phone"]), -r["confidence"]))
    return {
        "businesses_needing_contact_acquisition": len(rows),
        "by_route": dict(sorted(by_route.items(), key=lambda kv: -kv[1])),
        "headline": (f"{len(rows)} qualified businesses need contact acquisition "
                     f"before outreach" if rows else
                     "every actionable business has a usable contact"),
        "queue": rows[:max(1, limit)],
    }


def promote(db, limit: int = 0, only_ids: list | None = None) -> dict:
    """
    The half of the sweep that was missing.

    sweep() only ever demotes: it re-checks anything CLAIMING to be sendable
    and purges what fails. Nothing moved an address the other way, so 136
    addresses the harvester had genuinely read off a company's own website sat
    at DISCOVERED forever and the sendable count stayed at 4 — the four we had
    already emailed. Discovery without promotion is a funnel with no outlet.

    Promotion requires BOTH, because either alone has burned us:

      provenance  the address came from the business's own site, a founder
                  call, or a reply. Anything guessed stays where it is
                  regardless of how well it verifies — info@<company>.com
                  resolves beautifully and was never real.
      deliverability  MX resolves and the verifier does not call it INVALID
                  or disposable. CATCH_ALL is accepted: most Indian SMB
                  domains are catch-all, and refusing them was rejecting
                  most of the real book.

    A chain inbox is never promoted — hello@more.in reaching 26 branches is
    one relationship, not 26.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.email_verifier import verify_email
    from app.services.website_harvester import is_chain_inbox

    # Imported, not restated. This local pair was missing yahoo.co.in and the
    # eight hotel/restaurant role prefixes trust_promoter already knew, so the
    # two modules judged the same address differently.
    from app.services.identity import FREE_MAIL, ROLE_PREFIX
    TRUSTED_ORIGIN = ("WEBSITE", "FOUNDER_CALL", "EMAIL_REPLY", "DELIVERED")
    q = db.query(B2BLead).filter(B2BLead.email != "", B2BLead.email.isnot(None))
    if only_ids:
        q = q.filter(B2BLead.id.in_(only_ids))
    rows = [l for l in q.all()
            if (l.email_trust or "UNKNOWN").upper() not in SENDABLE + ("PURGED",)]
    if limit:
        rows = rows[:limit]

    out = {"considered": len(rows), "promoted": 0, "no_provenance": 0,
           "undeliverable": 0, "chain_inbox": 0, "not_a_business_address": 0,
           "promoted_rows": []}

    for l in rows:
        src = (getattr(l, "email_source", "") or "").upper()
        if src not in TRUSTED_ORIGIN:
            out["no_provenance"] += 1
            continue
        # How many OTHER businesses hold this exact address? That is the
        # signal — hello@more.in sitting on 26 branches is one relationship.
        shared = db.query(B2BLead).filter(
            B2BLead.email == l.email, B2BLead.id != l.id).count() + 1
        try:
            chain, why = is_chain_inbox(l.email, l.website or "", shared)
        except Exception as e:
            # Fail CLOSED. The first cut of this passed the session as the
            # second argument, every call raised, the except swallowed it, and
            # hello@more.in was promoted onto 26 businesses. A guard that
            # cannot run must block, never wave through.
            print(f"[promote] chain check errored for {l.email}: {e} — refusing")
            out["chain_inbox"] += 1
            continue
        if chain or shared > 1:
            out["chain_inbox"] += 1
            continue
        # A role prefix on free mail is not a business address. support@gmail.com
        # belongs to nobody and verifies perfectly.
        _lp, _, _dom = l.email.partition("@")
        if _dom.lower() in FREE_MAIL and _lp.lower() in ROLE_PREFIX:
            out["not_a_business_address"] += 1
            continue
        try:
            v = verify_email(l.email, l.company or "", getattr(l, "division", "") or "",
                             l.website or "")
        except Exception as e:
            print(f"[promote] verify failed for {l.email}: {e}")
            continue
        status = (v.get("status") or "UNVERIFIED").upper()
        if status in ("INVALID",) or v.get("is_disposable") or not v.get("mx_valid"):
            out["undeliverable"] += 1
            continue
        if status == "UNVERIFIED":       # inconclusive changes nothing
            continue
        grant(l, "VERIFIED", src, db,
              note=f"{status} conf={v.get('confidence')} — read from {src.lower()}")
        out["promoted"] += 1
        out["promoted_rows"].append(
            {"id": l.id, "company": l.company, "email": l.email,
             "status": status, "confidence": v.get("confidence")})
    db.commit()
    return out
