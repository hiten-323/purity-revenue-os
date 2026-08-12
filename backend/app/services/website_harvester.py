"""
Harvest real email addresses from businesses' own websites.

WHY THIS EXISTS
The system reported "0 sendable addresses" and I took that as the end of the
story — Google Places does not return emails, so there were none. That was
wrong. A quick test against 14 businesses that had a website returned 8 real
addresses. The addresses were there the whole time; nobody had looked.

The difference from the fabrication that got the Zoho account blocked is the
direction of travel. That code INVENTED contact@{company-name}.com and hoped.
This reads what the company published on its own contact page, keeps the URL it
came from, and then still makes it pass verification. An address either appears
on the business's own site or it does not exist here.

WHAT IS REJECTED, AND WHY
  - anything on a different domain than the website (a Wix or agency address)
  - placeholders — example@mysite.com is on a surprising number of templates
  - image files and tracking noise that regex-match an address
  - anything the verifier will not pass
"""
from __future__ import annotations

import re
from datetime import datetime

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Substrings that mean "not a real contact for this business".
_JUNK = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    "sentry", "wixpress", "wix.com", "godaddy", "squarespace",
    "example.com", "example@", "mysite.com", "domain.com", "yourdomain",
    "email@", "your@", "test@", "noreply", "no-reply", "donotreply",
    "sentry.io", "@2x", "u003e", "schema.org",
)

_PAGES = ("", "/contact", "/contact-us", "/contactus", "/about",
          "/about-us", "/reach-us", "/enquiry")


def _domain(url_or_email: str) -> str:
    s = (url_or_email or "").lower()
    s = re.sub(r"^https?://", "", s).split("@")[-1]
    return s.split("/")[0].replace("www.", "").strip()


def harvest_one(website: str, timeout: float = 12.0) -> list[dict]:
    """
    Read a business's own site and return the addresses it publishes.

    Only same-domain addresses count. A free-mail address (gmail, yahoo) is kept
    when it is published on the company's own contact page, because for small
    Indian businesses that IS the real address — but it is marked so the founder
    can see it is not on their domain.
    """
    import httpx

    site = (website or "").strip().rstrip("/")
    if not site:
        return []
    if not site.startswith("http"):
        site = "https://" + site
    host = _domain(site)
    out: dict[str, dict] = {}

    for path in _PAGES:
        try:
            r = httpx.get(site + path, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; PurityBeans/1.0)"})
            if r.status_code >= 400:
                continue
            for raw in EMAIL_RE.findall(r.text):
                addr = raw.lower().strip(".,;:")
                if len(addr) > 70 or any(j in addr for j in _JUNK):
                    continue
                dom = _domain(addr)
                free = dom in ("gmail.com", "yahoo.com", "yahoo.in", "hotmail.com",
                               "outlook.com", "rediffmail.com", "ymail.com")
                # Same domain as the site, or a free-mail address the business
                # itself published. Anything else belongs to somebody else.
                if dom != host and not free:
                    continue
                out.setdefault(addr, {
                    "email": addr,
                    "source_url": site + path,
                    "same_domain": dom == host,
                    "free_mail": free,
                })
        except Exception:
            continue
        if out:
            break        # the first page that yields something is enough
    return list(out.values())


def harvest(db, limit: int = 200, only_missing: bool = True) -> dict:
    """
    Harvest across every business that has a website, verify what comes back,
    and store the survivors with the URL they were found on.

    An address is stored VERIFIED only if the verifier passes it. One that is
    found but fails verification is recorded as DISCOVERED — we know it exists,
    and it still may not be sendable.
    """
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.email_verifier import verify_email
    from app.services.contact_trust import grant

    q = db.query(B2BLead).filter(B2BLead.website.isnot(None), B2BLead.website != "")
    leads = [l for l in q.all()
             if not only_missing or not (l.email or "").strip()][:limit]

    stats = {"sites_checked": 0, "addresses_found": 0,
             "stored_verified": 0, "stored_discovered": 0, "no_address": 0}
    results = []

    for l in leads:
        stats["sites_checked"] += 1
        hits = harvest_one(l.website)
        if not hits:
            stats["no_address"] += 1
            continue
        # Prefer a same-domain address over a published gmail.
        hits.sort(key=lambda h: (not h["same_domain"], len(h["email"])))
        best = hits[0]
        stats["addresses_found"] += 1

        try:
            v = verify_email(best["email"], l.company or "", l.website or "")
        except Exception:
            v = {"status": "UNVERIFIED", "reason": "verifier unavailable"}

        l.email = best["email"]
        if v.get("status") == "VALID":
            grant(l, "VERIFIED", "WEBSITE", db,
                  note=f"published on {best['source_url']}")
            stats["stored_verified"] += 1
        else:
            grant(l, "DISCOVERED", "WEBSITE", db,
                  note=f"found on {best['source_url']} but {v.get('status')}: {v.get('reason')}")
            stats["stored_discovered"] += 1
        l.email_collected_at = datetime.utcnow()
        db.add(WorkflowEvent(
            lead_id=l.id, event_type="CONTACT_DISCOVERED", actor="SYSTEM",
            channel="website",
            payload={"email": best["email"], "source_url": best["source_url"],
                     "same_domain": best["same_domain"],
                     "verification": v.get("status")},
            occurred_at=datetime.utcnow()))
        results.append({"lead_id": l.id, "company": l.company,
                        "email": best["email"], "source": best["source_url"],
                        "verification": v.get("status"),
                        "same_domain": best["same_domain"]})
        db.commit()

    # Chain inboxes can only be seen once the whole batch is in — the signal is
    # one address turning up on several businesses, which no single harvest can
    # know. Run it here so a harvest never leaves a head-office inbox sendable.
    chain = demote_chain_inboxes(db)
    stats["chain_inboxes_demoted"] = chain["demoted"]
    stats["stored_verified"] = max(0, stats["stored_verified"] - chain["demoted"])

    return {**stats, "results": results}


# ── Chain inboxes ────────────────────────────────────────────────────────────
# Harvesting a chain property's page returns the CHAIN's address, not the
# branch's. Domain matching cannot catch it: FabHotel Yellow Leaf's website IS
# fabhotels.com, so bookings@fabhotels.com looks perfectly legitimate.
#
# Two signals do catch it:
#   1. the same address appearing on MORE THAN ONE business — hello@more.in was
#      found on 26 separate More Supermarket branches. One inbox cannot be the
#      contact for 26 businesses; it is head office.
#   2. a website that is a deep path on a larger domain
#      (fabhotels.com/hotels-in-ludhiana/...) rather than the business's own
#      root — the branch does not own the domain, so it does not own the inbox.
#
# Mail to a chain's central inbox about supplying one branch is what gets marked
# as spam, and a complaint costs far more than a bounce.

CHAIN_ROLE_PREFIXES = ("bookings", "booking", "reservation", "reservations",
                       "feedback", "suggestion", "suggestions", "enquiries",
                       "enquiry", "events", "hello", "care", "customercare")


def is_chain_inbox(email: str, website: str, shared_count: int = 1) -> tuple[bool, str]:
    """Would this address reach head office rather than this business?"""
    if shared_count > 1:
        return True, (f"same address is on {shared_count} different businesses — "
                      f"a shared head-office inbox, not this branch")
    path_depth = (website or "").rstrip("/").count("/")
    local = (email or "").split("@")[0].lower()
    if path_depth > 3 and local in CHAIN_ROLE_PREFIXES:
        return True, ("website is a property page on a chain domain and the "
                      "address is a central role inbox")
    return False, ""


def demote_chain_inboxes(db) -> dict:
    """
    Find addresses shared across businesses and demote them out of sendable.

    They are kept, not deleted — the address is real, it simply is not a contact
    for THIS business. DISCOVERED records exactly that: known to exist, not
    sendable.
    """
    from collections import Counter
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.contact_trust import grant

    rows = [l for l in db.query(B2BLead).all() if (l.email or "").strip()]
    counts = Counter(l.email.lower().strip() for l in rows)

    demoted = []
    for l in rows:
        chain, why = is_chain_inbox(l.email, l.website or "",
                                    counts[l.email.lower().strip()])
        if not chain:
            continue
        if (l.email_trust or "") in ("FOUNDER_VERIFIED", "REPLIED"):
            continue          # the buyer's own word outranks this heuristic
        if (l.email_trust or "") == "DISCOVERED":
            continue          # already not sendable
        grant(l, "DISCOVERED", l.email_source or "WEBSITE", db,
              note=f"demoted: {why}")
        db.add(WorkflowEvent(
            lead_id=l.id, event_type="CHAIN_INBOX_DEMOTED", actor="SYSTEM",
            channel="trust", payload={"email": l.email, "reason": why},
            occurred_at=datetime.utcnow()))
        demoted.append({"company": l.company, "email": l.email, "why": why})
    db.commit()
    return {"demoted": len(demoted), "details": demoted[:20]}
