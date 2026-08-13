"""
Account resolution + company-level frequency governance.

The database holds leads. Businesses have accounts. Those are not the same
thing, and the gap between them is what let 26 More Supermarket branches look
like 26 opportunities sharing one inbox. Emailing all of them is not 26 times
the pipeline — it is one relationship, contacted 26 times in a day, by a
supplier who evidently does not know who they are talking to.

A cap keyed on the contact cannot prevent that: every one of the 26 rows is a
different contact. The cap has to be keyed on the ACCOUNT, so account
resolution is a prerequisite, not a later refinement.

Resolution is deliberately conservative. Merging two businesses that are NOT
the same suppresses outreach we were entitled to send, which costs revenue
silently. So a merge requires hard evidence — a shared registrable domain, or
a known chain brand with a matching name root — never a fuzzy name similarity.
When in doubt, they stay separate accounts.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

COOLDOWN_DAYS = 7          # one NEW contact per account per week
CHAIN_CORPORATE_FIRST_DAYS = 21   # branches wait while corporate has its turn

# Public suffixes we must not treat as the registrable domain, or every
# .co.in business in Punjab merges into one enormous account.
_MULTI = {"co.in", "net.in", "org.in", "gov.in", "ac.in", "co.uk", "com.au"}

# Free mail is never account evidence: two businesses both using gmail.com are
# not related. This is the single most dangerous over-merge available.
_FREE = {"gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com",
         "rediffmail.com", "live.com", "icloud.com",
         # A business whose "website" is its Instagram page shares nothing
         # with the next one. This merged 55 unrelated Punjab shops into one
         # account and would have suppressed outreach to all of them.
         "instagram.com", "facebook.com", "google.com", "linkedin.com",
         "youtube.com", "twitter.com", "x.com", "wa.me", "whatsapp.com",
         "business.site", "blogspot.com", "wordpress.com", "wixsite.com",
         "justdial.com", "indiamart.com", "tradeindia.com", "zomato.com",
         "swiggy.com", "sites.google.com",
         # Shared government infrastructure, not a corporate group: six
         # unrelated offices sat behind nic.in.
         "nic.in", "gov.in"}

# A brand root of one generic word is not evidence. "AB Enterprises" and
# "H K Agencies" are not the same company because both end in a category noun.
_GENERIC_ROOT = {"office", "enterprises", "enterprise", "agencies", "agency",
                 "traders", "trading", "foods", "food", "hotel", "hotels",
                 "restaurant", "caterers", "catering", "supermarket", "mart",
                 "general", "provision", "provisions", "sweets", "bakery",
                 "industries", "corporation", "services", "solutions",
                 "hospital", "school", "college", "institute", "college"}

_NOISE = re.compile(
    r"\b(pvt|private|ltd|limited|llp|inc|co|company|the|and|&|store|stores|"
    r"shop|branch|outlet|sector|phase|road|market|near|opp|opposite)\b", re.I)


def registrable(host: str) -> str:
    """example.co.in -> example.co.in ; www.a.example.com -> example.com"""
    h = (host or "").strip().lower()
    h = re.sub(r"^https?://", "", h).split("/")[0].split("?")[0]
    h = h[4:] if h.startswith("www.") else h
    parts = [p for p in h.split(".") if p]
    if len(parts) < 2:
        return h
    if ".".join(parts[-2:]) in _MULTI and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def name_root(company: str) -> str:
    """Brand root of a company name, with branch/location noise removed."""
    s = _NOISE.sub(" ", (company or "").lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if len(t) > 2]
    return " ".join(toks[:2])          # first two meaningful words = the brand


def account_key(lead) -> tuple[str, str]:
    """
    (key, basis). The basis is recorded so every merge can be explained —
    "why is this one account?" must always have an answer in the audit log.
    """
    site = registrable(getattr(lead, "website", "") or "")
    if site and site not in _FREE:
        return f"domain:{site}", f"shared website domain {site}"

    addr = (getattr(lead, "email", "") or "").strip().lower()
    dom = registrable(addr.split("@")[-1]) if "@" in addr else ""
    if dom and dom not in _FREE:
        return f"domain:{dom}", f"shared email domain {dom}"

    root = name_root(getattr(lead, "company", "") or "")
    toks = root.split()
    # Require TWO tokens with at least one distinctive: a single category noun
    # merges every trading house in Punjab into one account.
    if len(toks) >= 2 and any(t not in _GENERIC_ROOT for t in toks):
        return f"brand:{root}", f"brand root '{root}'"
    return f"lead:{lead.id}", "no grouping evidence — stands alone"


def resolve(db) -> dict:
    """Group every lead into accounts. Returns {key: {...}}."""
    from app.models.models import B2BLead
    accounts: dict = {}
    for l in db.query(B2BLead).all():
        k, basis = account_key(l)
        a = accounts.setdefault(k, {"key": k, "basis": basis, "leads": [],
                                    "companies": set(), "cities": set()})
        a["leads"].append(l)
        a["companies"].add(l.company or "")
        a["cities"].add(l.city or "")
    for a in accounts.values():
        a["branch_count"] = len(a["leads"])
        a["is_chain"] = a["branch_count"] >= 3
        a["name"] = sorted(a["companies"])[0] if a["companies"] else a["key"]
    return accounts


def account_for(lead, db) -> dict:
    """The account this lead belongs to, with its siblings."""
    from app.models.models import B2BLead
    k, basis = account_key(lead)
    sibs = [l for l in db.query(B2BLead).all() if account_key(l)[0] == k]
    return {"key": k, "basis": basis, "leads": sibs, "branch_count": len(sibs),
            "is_chain": len(sibs) >= 3,
            "name": sorted({l.company or "" for l in sibs})[0] if sibs
                    else (lead.company or "")}


# ── Frequency governance ─────────────────────────────────────────────────

def _events(db, lead_ids: list, types: list):
    from app.models.models import WorkflowEvent
    if not lead_ids:
        return []
    return db.query(WorkflowEvent).filter(
        WorkflowEvent.lead_id.in_(lead_ids),
        WorkflowEvent.event_type.in_(types)).all()


def can_contact_new(lead, db, founder_override: bool = False) -> tuple[bool, str]:
    """
    May we open a NEW conversation with this contact?

    This governs FIRST touches only. A contact already mid-sequence keeps its
    own cadence — stopping halfway through is worse than never starting, and
    the damage this prevents is breadth (26 branches at once), not depth.
    """
    from app.services.trust_promoter import REPLY_EVENTS

    acct = account_for(lead, db)
    ids = [l.id for l in acct["leads"]]

    if founder_override:
        return True, "founder override"

    # A human at this account has written to us. That does NOT open the
    # account up — it closes it to cold outreach. An earlier version returned
    # True here ("account is warm"), which is exactly backwards: the moment a
    # buyer engages is the moment you must stop cold-emailing five more people
    # at their company. The live conversation continues on its own contact;
    # everyone else waits for the founder to decide the account strategy.
    reps = _events(db, ids, list(REPLY_EVENTS))
    if reps:
        who = next((l.company for l in acct["leads"]
                    if l.id == reps[0].lead_id), "a contact")
        if any(e.lead_id == lead.id for e in reps):
            return True, f"{acct['name']}: this contact is in live conversation"
        return False, (f"{acct['name']}: {who[:30]} is already in conversation "
                       f"— cold outreach to the rest of the account is paused")

    # Both EMAIL_SENT and WHATSAPP_SENT are first-touch outreach channels.
    # The account cooldown must treat them the same, otherwise a WhatsApp
    # send to one branch leaves every other branch free for cold email the
    # same day.
    sends = sorted(_events(db, ids, ["EMAIL_SENT", "WHATSAPP_SENT"]),
                   key=lambda e: e.occurred_at or datetime.min)
    mine = [e for e in sends if e.lead_id == lead.id]
    if mine:
        return True, "already in sequence — cadence continues"

    if sends:
        last = sends[-1].occurred_at
        gap = (datetime.utcnow() - last).days if last else 999
        if gap < COOLDOWN_DAYS:
            other = next((l.company for l in acct["leads"]
                          if l.id == sends[-1].lead_id), "another contact")
            return False, (f"{acct['name']}: contacted {gap}d ago via "
                           f"{other[:30]} — {COOLDOWN_DAYS - gap}d of cooldown left")

        # Chain: corporate gets a clear run before we go to branches.
        if acct["is_chain"] and gap < CHAIN_CORPORATE_FIRST_DAYS:
            return False, (f"{acct['name']} is a {acct['branch_count']}-site "
                           f"chain — corporate contacted {gap}d ago, branches "
                           f"wait until day {CHAIN_CORPORATE_FIRST_DAYS}")
    return True, "ok"


def audit(db, top: int = 12) -> dict:
    """What the resolution actually did — every merge must be inspectable."""
    accts = resolve(db)
    multi = sorted([a for a in accts.values() if a["branch_count"] > 1],
                   key=lambda a: -a["branch_count"])
    return {"leads": sum(a["branch_count"] for a in accts.values()),
            "accounts": len(accts),
            "multi_site_accounts": len(multi),
            "chains": len([a for a in accts.values() if a["is_chain"]]),
            "largest": [{"name": a["name"], "branches": a["branch_count"],
                         "basis": a["basis"],
                         "cities": sorted(c for c in a["cities"] if c)[:6]}
                        for a in multi[:top]]}
