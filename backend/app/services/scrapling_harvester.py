"""Scrapling-backed first-party website enrichment.

This module is deliberately read-only. It can discover contact data published on
an account's own website and record provenance, but it cannot send email,
WhatsApp, or calls.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from app.services.scrapling_client import content_from_response, fetch_url

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}")
WALINK_RE = re.compile(
    r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(?:91)?(\d{10})", re.I
)
PAGES = ("", "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/reach-us", "/enquiry")
JUNK_EMAIL_PARTS = (
    "example", "sentry", "wixpress", "wix.com", "godaddy", "squarespace",
    "schema.org", "noreply", "no-reply", "donotreply", "yourdomain", "mysite.com",
)
FREE_MAIL = {
    "gmail.com", "yahoo.com", "yahoo.in", "hotmail.com", "outlook.com",
    "rediffmail.com", "ymail.com", "protonmail.com", "icloud.com",
}


def _domain(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"^https?://", "", value).split("@")[(-1)]
    return value.split("/")[0].replace("www.", "").strip()


def _site(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    return value if value.startswith("http") else f"https://{value}"


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91-{digits[:5]}-{digits[5:]}"
    return value.strip()


def _extract(html: str, website: str) -> dict[str, Any]:
    host = _domain(website)
    emails: list[str] = []
    for raw in EMAIL_RE.findall(html or ""):
        email = raw.lower().strip(".,;:")
        if any(j in email for j in JUNK_EMAIL_PARTS) or len(email) > 80:
            continue
        domain = _domain(email)
        if domain != host and domain not in FREE_MAIL:
            continue
        if email not in emails:
            emails.append(email)

    phones = []
    for raw in PHONE_RE.findall(html or ""):
        phone = _normalize_phone(raw)
        if phone not in phones:
            phones.append(phone)

    whatsapp = ""
    match = WALINK_RE.search(html or "")
    if match:
        whatsapp = _normalize_phone(match.group(1))

    # tel: links are useful when the page does not contain a formatted number.
    for raw in re.findall(r"tel:\s*([^\"'<>\s]+)", html or "", re.I):
        phone = _normalize_phone(raw)
        if phone and phone not in phones:
            phones.append(phone)

    return {"emails": emails[:8], "phones": phones[:8], "whatsapp": whatsapp}


def harvest_one(website: str) -> dict[str, Any]:
    """Read a first-party website through Scrapling, escalating fetch mode."""
    site = _site(website)
    if not site:
        return {"website": website, "emails": [], "phones": [], "whatsapp": "", "pages": [], "error": "missing website"}

    pages: list[dict[str, Any]] = []
    for suffix in PAGES:
        url = site + suffix
        response = None
        last_error = ""
        for mode in ("fast", "dynamic", "stealth"):
            try:
                if mode == "fast":
                    response = fetch_url(url)
                elif mode == "dynamic":
                    response = fetch_url(url, dynamic=True)
                else:
                    if os.getenv("SCRAPLING_ALLOW_STEALTH", "0").strip().lower() not in {"1", "true", "yes", "on"}:
                        continue
                    response = fetch_url(url, stealth=True)
                content = content_from_response(response)
                status = response.get("status")
                if content and (status is None or int(status) < 400):
                    hit = _extract(content, site)
                    pages.append({"url": url, "mode": mode, "status": status, **hit})
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

        if pages and (pages[-1]["emails"] or pages[-1]["phones"] or pages[-1]["whatsapp"]):
            break

    emails: list[str] = []
    phones: list[str] = []
    whatsapp = ""
    for page in pages:
        for value in page["emails"]:
            if value not in emails:
                emails.append(value)
        for value in page["phones"]:
            if value not in phones:
                phones.append(value)
        if not whatsapp and page["whatsapp"]:
            whatsapp = page["whatsapp"]

    return {
        "website": site,
        "emails": emails[:8],
        "phones": phones[:8],
        "whatsapp": whatsapp,
        "pages": pages,
        "error": "" if pages else last_error,
    }


def harvest(db, limit: int = 20, only_missing: bool = True) -> dict[str, Any]:
    """Enrich a bounded batch of leads with first-party website evidence."""
    from app.models.models import B2BLead, WorkflowEvent
    from app.services.contact_trust import grant
    from app.services.email_verifier import verify_email

    if os.getenv("SCRAPLING_ENABLED", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"enabled": False, "processed": 0, "found": 0, "results": []}

    q = db.query(B2BLead).filter(B2BLead.website.isnot(None), B2BLead.website != "")
    leads = q.order_by(B2BLead.id.asc()).all()
    if only_missing:
        leads = [lead for lead in leads if not (lead.email or "").strip() or not (lead.phone or "").strip()]
    leads = leads[: max(1, min(int(limit), 100))]

    results = []
    for lead in leads:
        try:
            hit = harvest_one(lead.website)
        except Exception as exc:
            results.append({"lead_id": lead.id, "company": lead.company, "error": str(exc)})
            continue

        changed = False
        best_email = hit["emails"][0] if hit["emails"] else ""
        if best_email and not (lead.email or "").strip():
            lead.email = best_email
            lead.email_collected_at = datetime.utcnow()
            try:
                verification = verify_email(best_email, lead.company or "", lead.website or "")
            except Exception:
                verification = {"status": "UNVERIFIED", "reason": "verifier unavailable"}
            trust = "VERIFIED" if verification.get("status") == "VALID" else "DISCOVERED"
            grant(lead, trust, "WEBSITE", db, note="published on first-party website via Scrapling")
            changed = True
        elif best_email and (lead.email or "").strip().lower() == best_email.lower():
            verification = {"status": "EXISTING", "reason": "email already stored"}
        else:
            verification = {"status": "SKIPPED", "reason": "no email discovered"}

        best_phone = hit["whatsapp"] or (hit["phones"][0] if hit["phones"] else "")
        if best_phone and not (lead.phone or "").strip():
            lead.phone = best_phone
            lead.whatsapp_number = hit["whatsapp"] or best_phone
            lead.phone_source = "WEBSITE"
            lead.phone_verified = True
            lead.contact_searched_at = datetime.utcnow()
            changed = True

        if changed:
            db.add(WorkflowEvent(
                lead_id=lead.id,
                event_type="CONTACT_DISCOVERED",
                actor="SYSTEM",
                channel="web_research",
                payload={
                    "provider": "Scrapling",
                    "website": hit["website"],
                    "pages": [p["url"] for p in hit["pages"]],
                    "emails_found": hit["emails"],
                    "phones_found": hit["phones"],
                    "whatsapp": hit["whatsapp"],
                    "email_verification": verification.get("status"),
                },
                occurred_at=datetime.utcnow(),
            ))
            db.commit()

        results.append({
            "lead_id": lead.id,
            "company": lead.company,
            "website": hit["website"],
            "email": best_email,
            "phone": best_phone,
            "pages": len(hit["pages"]),
            "changed": changed,
            "error": hit["error"],
        })

    return {"enabled": True, "processed": len(leads), "found": sum(1 for r in results if r.get("changed")), "results": results}
