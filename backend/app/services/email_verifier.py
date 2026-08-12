"""
Email verification service.
Checks MX records, disposable domains, format validity, and domain-company alignment.
Returns a confidence score 0-100. Anything below 85 is flagged for manual approval.
Government leads always require manual approval regardless of score.
"""
import re
import socket
import dns.resolver  # pip install dnspython
from typing import Optional

# ── Disposable / Blacklisted domains ──────────────────────────────────────────
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "trashmail.com", "dispostable.com",
    "fakeinbox.com", "maildrop.cc", "mailnull.com", "spamgourmet.com",
    "throwam.com", "10minutemail.com", "minutemail.com", "getairmail.com",
    "spamherelots.com", "tempr.email", "discard.email",
}

# Prefixes that are likely generic / scraper artefacts (low confidence)
GENERIC_PREFIXES = {
    "info", "contact", "hello", "webmaster", "postmaster", "abuse", "test",
    "noreply", "no-reply", "donotreply", "do-not-reply", "enquiry", "enquiries",
}

# Known legitimate Indian gov / psu domains (boost confidence)
GOV_DOMAINS = {
    "gov.in", "nic.in", "ias.nic.in", "gem.gov.in", "irctc.co.in",
    "csd.gov.in", "csdindia.gov.in", "kendriyabhandar.net",
    "ntpc.co.in", "ntpc.com", "powergrid.in", "sbi.co.in",
    "aai.aero", "coalindia.in", "ongc.co.in", "iocl.com",
    "bhel.com", "hpcl.co.in", "bpcl.in",
}

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _check_mx(domain: str, timeout: float = 5.0) -> bool:
    """
    True if the domain can receive mail. Uses PUBLIC DNS (8.8.8.8/1.1.1.1) — the
    host's default nameserver is unreliable and silently times out, which was
    letting fabricated/dead domains pass verification. NXDOMAIN → definitively
    False (domain does not exist).
    """
    try:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
        resolver.lifetime = timeout
        resolver.timeout = timeout
        for rtype in ("MX", "A"):
            try:
                if resolver.resolve(domain, rtype):
                    return True
            except dns.resolver.NXDOMAIN:
                return False
            except dns.resolver.NoAnswer:
                continue
        return False
    except dns.resolver.NXDOMAIN:
        return False
    except Exception:
        # Public DNS unreachable — fall back to the OS resolver (A record).
        return _check_mx_fallback(domain)


def _check_mx_fallback(domain: str) -> bool:
    """Fallback: check if domain resolves at all (A record)."""
    try:
        socket.setdefaulttimeout(3)
        socket.getaddrinfo(domain, None)
        return True
    except Exception:
        return False


def verify_email(
    email: str,
    company_name: str = "",
    division: str = "",
    website: str = "",
) -> dict:
    """
    Verify an email address and return a verification result dict.

    Result keys:
      status       : VALID | CATCH_ALL | RISKY | INVALID | UNVERIFIED
      confidence   : 0-100
      mx_valid     : bool
      is_disposable: bool
      is_generic   : bool
      domain_match : bool
      reason       : human-readable explanation
      requires_approval: bool  (always True for gov, True when confidence < 90)
    """
    email = (email or "").strip().lower()

    if not email or not EMAIL_RE.match(email):
        return _result("INVALID", 0, False, False, False, False, "Invalid email format", division)

    local, domain = email.rsplit("@", 1)

    # ── 1. Disposable domain check ────────────────────────────────────────────
    if domain in DISPOSABLE_DOMAINS:
        return _result("INVALID", 0, False, True, False, False, "Disposable email domain", division)

    # ── 2. MX record check ───────────────────────────────────────────────────
    mx_ok = _check_mx(domain)
    if not mx_ok:
        mx_ok = _check_mx_fallback(domain)  # fallback A-record check
    if not mx_ok:
        return _result("INVALID", 5, False, False, False, False, "No MX / DNS record found for domain", division)

    # ── 3. Generic prefix check ──────────────────────────────────────────────
    is_generic = local in GENERIC_PREFIXES

    # ── 4. Domain-company alignment ──────────────────────────────────────────
    domain_match = _domain_matches_company(domain, company_name, website)

    # ── 5. Gov domain bonus ──────────────────────────────────────────────────
    is_gov_domain = any(domain.endswith(gd) for gd in GOV_DOMAINS)

    # ── 6. Confidence scoring ────────────────────────────────────────────────
    score = 60  # base: format + MX ok

    if domain_match:
        score += 20
    if is_gov_domain:
        score += 10
    if not is_generic:
        score += 10
    if is_generic:
        score -= 15
    if not domain_match and not is_gov_domain:
        score -= 10

    score = max(0, min(100, score))

    # ── 7. Status mapping ────────────────────────────────────────────────────
    if score >= 85:
        status = "VALID"
        reason = "MX record valid, domain matches, high confidence"
    elif score >= 60:
        status = "CATCH_ALL"
        reason = "MX record valid but email cannot be fully verified — likely deliverable"
    else:
        status = "RISKY"
        reason = "Low confidence — generic prefix or domain mismatch"

    if is_generic:
        reason = f"Generic email prefix '{local}' — may not reach a decision maker"

    return _result(status, score, mx_ok, False, is_generic, domain_match, reason, division)


def _domain_matches_company(domain: str, company: str, website: str) -> bool:
    """Rough check: does the email domain relate to the company name or website?"""
    if not company:
        return False

    # Strip TLD from domain for comparison
    domain_core = domain.split(".")[0].lower()
    company_words = re.sub(r"[^a-z0-9 ]", "", company.lower()).split()

    # Website check
    if website:
        site = re.sub(r"https?://(www\.)?", "", website.lower()).split("/")[0]
        if domain in site or site in domain:
            return True

    # Company name words in domain
    for word in company_words:
        if len(word) >= 4 and word in domain_core:
            return True

    return False


def _result(
    status: str,
    confidence: int,
    mx_valid: bool,
    is_disposable: bool,
    is_generic: bool,
    domain_match: bool,
    reason: str,
    division: str,
) -> dict:
    requires_approval = (
        division in ("government", "gov")
        or confidence < 90
        or status in ("RISKY", "CATCH_ALL")
    )
    return {
        "status": status,
        "confidence": confidence,
        "mx_valid": mx_valid,
        "is_disposable": is_disposable,
        "is_generic": is_generic,
        "domain_match": domain_match,
        "reason": reason,
        "requires_approval": requires_approval,
    }


def generate_email_preview(lead) -> dict:
    """
    Generate a personalized outreach email preview for a lead.
    Returns {subject, body, to_name, to_email}.
    """
    name = lead.contact_name or "Procurement Team"
    company = lead.company or "your organization"
    division = (lead.division or "corporate").lower()
    city = lead.city or "your city"

    # ── Subject line by division ──────────────────────────────────────────────
    if division == "government":
        subject = f"Premium Instant Coffee Supply — {company}"
    elif division == "distributor":
        subject = "Earn 30% Margin with a Fast-Growing Premium Coffee Brand"
    elif division in ("horeca", "hotel", "cafe", "restaurant"):
        subject = f"Specialty Coffee Supply for {company} — Purity Beans"
    elif division == "gifting":
        subject = f"Corporate Coffee Gift Sets — Purity Beans × {company}"
    else:
        subject = f"100% Pure Instant Coffee Supply — {company}"

    # ── Greeting ──────────────────────────────────────────────────────────────
    greeting = f"Dear {name},"

    # ── Body by division ─────────────────────────────────────────────────────
    if division == "government":
        body = f"""{greeting}

I am reaching out from Pure Pantry Provisions, manufacturers of Purity Beans — 100% pure instant coffee with zero chicory.

We are registered on GeM (Government e-Marketplace) and supply to institutional buyers across India. We noticed that {company} procures coffee for institutional consumption and would like to offer our products for your consideration.

Our Institutional Range:
  • Purista — Freeze-Dried Arabica Blend (premium)
  • Purica — Freeze-Dried Robusta (cost-optimised)
  • Ultra Blend & Bold — Agglomerated variants

All products are FSSAI-certified, BIS-compliant, and available in bulk institutional packs (1kg, 5kg, 10kg). GeM Seller ID available on request.

I would be happy to share our product catalogue, FSSAI certificate, and GeM listing. May I schedule a brief 15-minute call at your convenience?

Warm Regards,
Hiten Jain
Founder | Pure Pantry Provisions
+91 90849 58495 | connect@purepantryprovisions.com
p3online.in"""

    elif division == "distributor":
        body = f"""{greeting}

A few years ago, I spent nearly three months on a ventilator in a coma. During recovery, I became deeply conscious of what I consumed every day — that journey inspired me to create Purity Beans.

We are now expanding pan-India and looking for distribution partners in {city}.

Purity Beans at a Glance:
  • 100% Pure Coffee — Zero Chicory, Zero Fillers
  • Food-Grade Lead-Free Glass Jars (50 gm & 100 gm)
  • 28% Trade + 2% Cash Discount = 30% Effective Margin
  • SKUs: Purista · Purica · Ultra Blend · Bold

I would be happy to send a complimentary sample kit before our discussion — no obligation.

Would you be available for a 15-minute call this week?

Warm Regards,
Hiten Jain
Founder | Pure Pantry Provisions
+91 90849 58495 | connect@purepantryprovisions.com"""

    else:
        body = f"""{greeting}

I am reaching out from Pure Pantry Provisions, proud manufacturers of Purity Beans — 100% pure instant coffee with zero chicory, zero fillers.

We noticed that {company} may benefit from a reliable premium coffee supply for {city}-based operations.

Why Purity Beans?
  • 100% Pure — no chicory, no fillers, no compromises
  • FSSAI certified · BIS compliant
  • Available in institutional packs and premium glass jars
  • Next-day dispatch on repeat orders

I would love to send a complimentary sample kit to your office — absolutely no obligation — so your team can experience the quality firsthand.

Would you be open to a 15-minute conversation this week?

Warm Regards,
Hiten Jain
Founder | Pure Pantry Provisions
+91 90849 58495 | connect@purepantryprovisions.com
p3online.in"""

    return {
        "to_name": name,
        "to_email": lead.email or "",
        "subject": subject,
        "body": body,
    }
