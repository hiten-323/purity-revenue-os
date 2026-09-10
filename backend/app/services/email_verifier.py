"""
Email verification service.
Checks MX records, disposable domains, format validity, and domain-company alignment.
Returns a confidence score 0-100. Anything below 85 is flagged for manual approval.
Government leads always require manual approval regardless of score.
"""
import re
import socket
from typing import Optional

try:
    import dns.resolver  # pip install dnspython

    _HAS_DNS = True
except ImportError:  # pragma: no cover
    dns = None  # type: ignore
    _HAS_DNS = False

# ── Disposable / Blacklisted domains ──────────────────────────────────────────
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "trashmail.com", "dispostable.com",
    "fakeinbox.com", "maildrop.cc", "mailnull.com", "spamgourmet.com",
    "throwam.com", "10minutemail.com", "minutemail.com", "getairmail.com",
    "spamherelots.com", "tempr.email", "discard.email",
}

GENERIC_PREFIXES = {
    "info", "contact", "hello", "webmaster", "postmaster", "abuse", "test",
    "noreply", "no-reply", "donotreply", "do-not-reply", "enquiry", "enquiries",
}

GOV_DOMAINS = {
    "gov.in", "nic.in", "ias.nic.in", "gem.gov.in", "irctc.co.in",
    "csd.gov.in", "csdindia.gov.in", "kendriyabhandar.net",
    "ntpc.co.in", "ntpc.com", "powergrid.in", "sbi.co.in",
    "aai.aero", "coalindia.in", "ongc.co.in", "iocl.com",
    "bhel.com", "hpcl.co.in", "bpcl.in",
}

# Anchored: this module validates ONE address, it does not scan text.
from app.services.identity import EMAIL_EXACT_RE as EMAIL_RE


def _check_mx(domain: str, timeout: float = 5.0) -> bool:
    """True if the domain can receive mail. Falls back to A-record when dns missing."""
    if not _HAS_DNS:
        return _check_mx_fallback(domain)
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
        return _check_mx_fallback(domain)


def _check_mx_fallback(domain: str) -> bool:
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
    email = (email or "").strip().lower()

    if not email or not EMAIL_RE.match(email):
        return _result("INVALID", 0, False, False, False, False, "Invalid email format", division)

    local, domain = email.rsplit("@", 1)

    if domain in DISPOSABLE_DOMAINS:
        return _result("INVALID", 0, False, True, False, False, "Disposable email domain", division)

    mx_ok = _check_mx(domain)
    if not mx_ok:
        mx_ok = _check_mx_fallback(domain)
    if not mx_ok:
        return _result("INVALID", 5, False, False, False, False, "No MX / DNS record found for domain", division)

    is_generic = local in GENERIC_PREFIXES
    domain_match = _domain_matches_company(domain, company_name, website)
    is_gov_domain = any(domain.endswith(gd) for gd in GOV_DOMAINS)

    score = 60
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
    if not company:
        return False
    domain_core = domain.split(".")[0].lower()
    company_words = re.sub(r"[^a-z0-9 ]", "", company.lower()).split()
    if website:
        site = re.sub(r"https?://(www\.)?", "", website.lower()).split("/")[0]
        if domain in site or site in domain:
            return True
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
    name = lead.contact_name or "Procurement Team"
    # Customer-facing name, never the raw directory string. Nine subject
    # templates across four modules interpolate this, and one produced
    # "A sample for WrkPod | Coworking Space in Coimbatore | Shared Office
    # Space?" as a live subject. Cleaning at each derivation keeps the raw
    # value on the record for provenance while nothing customer-facing sees it.
    from app.services.lead_quality import display_name
    company = display_name(lead.company or "") or "your organization"
    division = (lead.division or "corporate").lower()
    city = lead.city or "your city"

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

    greeting = f"Dear {name},"
    body = (
        f"{greeting}\n\nI am reaching out from Pure Pantry Provisions regarding "
        f"Purity Beans for {company} in {city}.\n\nWould you like the catalogue?\n\n"
        f"Warm Regards,\nHiten Jain\nPure Pantry Provisions"
    )
    return {
        "to_name": name,
        "to_email": lead.email or "",
        "subject": subject,
        "body": body,
    }
