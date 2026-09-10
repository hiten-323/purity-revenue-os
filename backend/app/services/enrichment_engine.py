"""
Enrichment Engine — finds website, extracts email and decision-maker contact
from publicly available business sources before outreach.

Pipeline:
  Company Name + City → Google search → Website → Extract email + phone → Confidence score
"""
import re
import socket
import time
import random
import requests
from typing import Optional
import logging

_log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

from app.services.identity import EMAIL_RE, PHONE_RE

SKIP_TLDS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com"}


def enrich_lead(company: str, city: str = "", website: str = "", division: str = "") -> dict:
    """
    Enrich a single lead. Returns:
      {
        website, emails, phones,
        decision_maker_email, decision_maker_name,
        enrichment_confidence, enrichment_notes
      }
    """
    result = {
        "website": website or None,
        "emails": [],
        "phones": [],
        "decision_maker_email": None,
        "decision_maker_name": None,
        "enrichment_confidence": 0,
        "enrichment_notes": [],
    }

    # Step 1: Find website if not already known
    if not website:
        found = _find_website(company, city)
        if found:
            result["website"] = found
            result["enrichment_notes"].append(f"Website found: {found}")

    # Step 2: Extract emails from website
    if result["website"]:
        emails, phones = _extract_from_website(result["website"])
        result["emails"] = emails
        result["phones"] = phones
        if emails:
            result["enrichment_notes"].append(f"Extracted {len(emails)} email(s) from website")
        if phones:
            result["enrichment_notes"].append(f"Extracted {len(phones)} phone(s) from website")

    # Step 3: Score decision maker email
    if result["emails"]:
        dm_email = _pick_decision_maker_email(result["emails"], company)
        result["decision_maker_email"] = dm_email
        result["enrichment_confidence"] = _score_confidence(result)

    return result


def _find_website(company: str, city: str) -> Optional[str]:
    """Search for the company's official website via DuckDuckGo HTML search."""
    query = f"{company} {city} official website"
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        # Extract first non-ad result link
        links = re.findall(r'href="(https?://[^"]+)"', resp.text)
        for link in links:
            domain = re.sub(r"https?://(www\.)?", "", link).split("/")[0].lower()
            # Skip search engines, social media, directories
            if any(skip in domain for skip in [
                "duckduckgo", "google", "bing", "indiamart", "facebook",
                "twitter", "linkedin", "justdial", "sulekha", "tradeindia",
                "wikipedia", "amazon", "flipkart", "youtube",
            ]):
                continue
            return link.split("?")[0]
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return None


def _extract_from_website(url: str) -> tuple[list[str], list[str]]:
    """Extract emails and phones from a website's homepage and contact page."""
    emails: set[str] = set()
    phones: set[str] = set()

    pages = [url]
    # Also try /contact and /about
    base = url.rstrip("/")
    pages += [f"{base}/contact", f"{base}/contact-us", f"{base}/about"]

    for page in pages[:3]:
        try:
            r = requests.get(page, headers=HEADERS, timeout=6, allow_redirects=True)
            if r.status_code != 200:
                continue
            text = r.text
            found_emails = EMAIL_RE.findall(text)
            found_phones = PHONE_RE.findall(text)

            for e in found_emails:
                e = e.lower().strip(".,;:\"'")
                domain = e.split("@")[-1]
                if domain not in SKIP_TLDS and "example" not in e:
                    emails.add(e)
            for p in found_phones:
                clean = re.sub(r"[-\s]", "", p)
                if len(clean) >= 10:
                    phones.add(clean)

            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            continue

    return sorted(emails)[:5], sorted(phones)[:3]


def _pick_decision_maker_email(emails: list[str], company: str) -> Optional[str]:
    """
    Prefer emails with decision-maker prefixes over generic ones.
    Order: owner/md/ceo > info/contact > other.
    """
    dm_prefixes = ["owner", "md", "ceo", "director", "head", "manager", "partner",
                   "founder", "admin", "procurement", "purchase", "sales"]
    generic_prefixes = ["info", "contact", "hello", "enquiry", "support"]

    dm_emails = [e for e in emails if any(e.startswith(p) for p in dm_prefixes)]
    generic = [e for e in emails if any(e.startswith(p) for p in generic_prefixes)]
    other = [e for e in emails if e not in dm_emails and e not in generic]

    if dm_emails:
        return dm_emails[0]
    if generic:
        return generic[0]
    if other:
        return other[0]
    return emails[0] if emails else None


def _score_confidence(result: dict) -> int:
    score = 0
    if result["website"]:
        score += 30
    if result["emails"]:
        score += 40
    if result["phones"]:
        score += 20
    if result["decision_maker_email"]:
        score += 10
    return min(100, score)


def enrich_batch(leads, db, max_per_run: int = 30) -> dict:
    """
    Enrich a batch of leads. Updates lead.website and lead.email if missing.
    Logs a LEAD_ENRICHED WorkflowEvent per lead.
    Returns summary stats.
    """
    from app.services.pipeline_tracker import track

    enriched = 0
    skipped = 0
    failed = 0

    for lead in leads[:max_per_run]:
        # Skip if already has email and website
        if lead.email and lead.website:
            skipped += 1
            continue

        try:
            result = enrich_lead(
                company=lead.company or "",
                city=lead.city or "",
                website=lead.website or "",
                division=lead.division or "",
            )

            changed = False
            if result["website"] and not lead.website:
                lead.website = result["website"]
                changed = True
            if result["decision_maker_email"] and not lead.email:
                lead.email = result["decision_maker_email"]
                changed = True
            if result["phones"] and not lead.phone:
                lead.phone = result["phones"][0]
                changed = True

            if changed:
                track(
                    db, "LEAD_ENRICHED", lead_id=lead.id, actor="SYSTEM",
                    payload={
                        "website": result["website"],
                        "emails_found": result["emails"],
                        "phones_found": result["phones"],
                        "confidence": result["enrichment_confidence"],
                        "notes": result["enrichment_notes"],
                    },
                )
                enriched += 1
            else:
                skipped += 1

            time.sleep(random.uniform(0.5, 1.2))

        except Exception as exc:
            failed += 1
            track(
                db, "WORKFLOW_FAILED", lead_id=lead.id, actor="SYSTEM",
                payload={"step": "enrichment", "error": str(exc)[:200]},
            )

    db.commit()
    return {"enriched": enriched, "skipped": skipped, "failed": failed}
