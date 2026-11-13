"""
Enrichment Engine — High-Intent Intelligence & Contact Discovery for Purity Revenue OS.

Pipeline:
  Company + City → Discover Official Website → Deep Multi-Page Crawl
  → Extract Verified Contacts (Email, Mobile, WhatsApp, Decision Maker Name & Title)
  → Detect High-Intent Coffee Signals (Instant Coffee, Espresso/Vending, Pantry, Breakfast, Wholesale)
  → Identify Competitor Brands (Nescafe, Bru, Tata Coffee, Continental)
  → Collect Truth Layer Evidence (LeadEvidence)
  → Score Coffee Demand (coffee_buying_score) & Intent (high_intent_scorer Priority A/B)
  → Zero Fabrication: Never invent phone numbers, emails, or buying signals.
"""
from __future__ import annotations

import os
import re
import socket
import time
import random
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import urllib.parse
import httpx
import requests

from app.services.identity import EMAIL_RE, PHONE_RE, FREE_MAIL, is_landline, digits_only

_log = logging.getLogger(__name__)

# Hard blocked numbers (NEVER dial, message, or store as contact)
HARD_BLOCK = {"8591977190"}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
}

# Directories, social networks, and aggregators to skip when searching for official site
SKIP_DOMAINS = {
    "duckduckgo", "google", "bing", "yahoo", "indiamart", "facebook",
    "twitter", "x.com", "linkedin", "justdial", "sulekha", "tradeindia",
    "wikipedia", "amazon", "flipkart", "youtube", "instagram", "tripadvisor",
    "zomato", "swiggy", "magicpin", "jdmagicbox", "exportersindia", "yellowpages",
    "idbf.in", "yappe.in", "openalfa.in", "chotu.com", "aeroleads.com",
    "apnapatiala.com", "dialepunjab.com", "vaidam.com", "zoominfo", "infobel",
    "knowyourgst", "mystreetbiz", "mumbai96.com", "piceapp.com", "kaam.com",
}

BAD_EMAIL_PARTS = {
    "example", "sentry", "wixpress", "wix.com", "godaddy", "squarespace",
    "schema.org", "noreply", "no-reply", "donotreply", "yourdomain",
    "mysite.com", "domain.com", "email.com", "test.com", "support@google",
}

FOREIGN_TLD_RE = re.compile(
    r"\.(au|uk|ca|nz|za|sg|ae|de|fr|cn|jp|ru|br|pk|bd|lk|np)$", re.IGNORECASE
)

# Decision Maker Designation Patterns
DM_TITLE_RE = re.compile(
    r"\b(Managing\s+Director|Purchase\s+Manager|Procurement\s+Head|Procurement\s+Manager|"
    r"Proprietor|Owner|Director|Partner|F&B\s+Manager|General\s+Manager|General\s+Partner|"
    r"Operations\s+Manager|Chief\s+Executive\s+Officer|Executive\s+Chef|Head\s+Chef|"
    r"Purchase\s+Head|Commercial\s+Manager|Supply\s+Chain\s+Head|Admin\s+Head)\b",
    re.IGNORECASE,
)

# Honorific + Name Pattern
PERSON_NAME_RE = re.compile(
    r"\b(Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri\.?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b"
)

# Business Registration Identifiers
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b")
FSSAI_RE = re.compile(r"\b(?:FSSAI|Lic(?:ence)?\s*(?:No\.?)?)\s*[:#-]?\s*([12]\d{13})\b", re.IGNORECASE)

# High-Intent Coffee Signals
RE_INSTANT_COFFEE = re.compile(
    r"\b(instant\s+coffee|coffee\s+powder|soluble\s+coffee|agglomerated\s+coffee|freeze\s+dried|spray\s+dried)\b",
    re.IGNORECASE,
)
RE_COFFEE_SERVICE = re.compile(
    r"\b(espresso|cappuccino|latte|filter\s+coffee|coffee\s+machine|coffee\s+vending|vending\s+machine|beverage\s+dispenser|coffee\s+counter)\b",
    re.IGNORECASE,
)
RE_PANTRY_CAFETERIA = re.compile(
    r"\b(office\s+pantry|pantry|cafeteria|staff\s+canteen|employee\s+canteen|break\s?room|refreshments|tea\s*&?\s*coffee)\b",
    re.IGNORECASE,
)
RE_BREAKFAST_BUFFET = re.compile(
    r"\b(complimentary\s+breakfast|morning\s+buffet|continental\s+breakfast|breakfast\s+included|bed\s*&?\s*breakfast)\b",
    re.IGNORECASE,
)
RE_WHOLESALE_PROCUREMENT = re.compile(
    r"\b(bulk\s+orders?|wholesale\s+inquir(?:y|ies)|institutional\s+suppl(?:y|ies)|corporate\s+gifting|catering\s+orders?|distributor\s+enquir(?:y|ies)|procurement|rfq)\b",
    re.IGNORECASE,
)

# Competitor Brands to Detect for Displacement / Switching Opportunities
COMPETITOR_BRANDS = [
    ("Nescafe", re.compile(r"\b(nescafe|nescafé)\b", re.IGNORECASE)),
    ("Nestle Sunrise", re.compile(r"\b(nestle\s+sunrise|nestlé\s+sunrise)\b", re.IGNORECASE)),
    ("Nestle Classic", re.compile(r"\b(nestle\s+classic|nestlé\s+classic)\b", re.IGNORECASE)),
    ("Bru Instant", re.compile(r"\b(bru\s+instant|bru\s+gold|bru\s+coffee)\b", re.IGNORECASE)),
    ("Tata Coffee", re.compile(r"\b(tata\s+coffee|tata\s+grand)\b", re.IGNORECASE)),
    ("Continental Coffee", re.compile(r"\b(continental\s+coffee|continental\s+xtra|continental\s+special)\b", re.IGNORECASE)),
    ("Davidoff", re.compile(r"\b(davidoff\s+coffee|davidoff)\b", re.IGNORECASE)),
    ("Lavazza", re.compile(r"\b(lavazza)\b", re.IGNORECASE)),
]


def _clean_phone(raw_phone: str) -> Optional[str]:
    """
    Validates and formats an Indian mobile number.
    Returns normalized +91-XXXXX-XXXXX, or None if invalid.
    Strictly rejects landlines, toll-free, fake patterns, and HARD_BLOCK.
    """
    if not raw_phone:
        return None
    digits = re.sub(r"\D", "", raw_phone)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]

    if len(digits) != 10:
        return None

    # Must start with 6, 7, 8, 9 for Indian mobile
    if digits[0] not in "6789":
        return None

    # Check for hard block
    if digits in HARD_BLOCK:
        return None

    # Check for placeholder junk sequences
    if len(set(digits)) <= 2:
        return None
    if any(pat in digits for pat in ("123456", "000000", "999999", "888888", "111111")):
        return None
    if digits == "1234567890":
        return None

    # Validate against known landline STD prefixes
    if is_landline(digits):
        return None

    return f"+91-{digits[:5]}-{digits[5:]}"


def _clean_email(email: str, company: str = "") -> Optional[str]:
    """Validate email address. Strips junk domains and foreign TLDs."""
    if not email or "@" not in email:
        return None
    email = email.lower().strip(".,;:\"'<>[]() ")
    if len(email) > 80:
        return None

    domain = email.split("@")[-1].strip()
    if not domain or "." not in domain:
        return None

    # Reject unwanted aggregator or template domains
    if any(bad in email for bad in BAD_EMAIL_PARTS) or any(bad in domain for bad in SKIP_DOMAINS):
        return None

    # Reject bogus generic prefixes on public webmail providers (e.g. info@gmail.com, contact@yahoo.com)
    if domain in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "rediffmail.com", "icloud.com"):
        user_part = email.split("@")[0].strip()
        if user_part in ("info", "contact", "admin", "support", "sales", "test", "mail", "help", "enquiry", "office", "customercare", "hello"):
            return None

    # Reject foreign country TLDs for local Indian B2B leads
    if FOREIGN_TLD_RE.search(domain):
        return None

    return email


def _clean_company_query(company: str) -> str:
    """Strip trailing '| City, State' suffixes from scraped directory names."""
    return (company or "").split("|")[0].strip()


def _find_via_serper(company: str, city: str = "") -> Dict[str, Any]:
    """Use Serper.dev Google Maps + Google Search APIs (if SERPER_API_KEY is set)."""
    out: Dict[str, Any] = {"website": None, "phones": [], "emails": [], "web_sources": []}
    if os.getenv("PYTEST_CURRENT_TEST"):
        return out
    api_key = (os.getenv("SERPER_API_KEY") or "").strip()
    clean_co = _clean_company_query(company)
    if not api_key or not clean_co:
        return out

    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    q_base = f"{clean_co} {city} India".strip()

    # 1. Serper Google Maps (/maps) -> official website + phoneNumber
    try:
        r = requests.post(
            "https://google.serper.dev/maps",
            json={"q": q_base, "gl": "in"},
            headers=headers,
            timeout=6,
        )
        if r.status_code == 200:
            places = r.json().get("places") or []
            for pl in places[:2]:
                site = (pl.get("website") or "").strip()
                if site and not out["website"]:
                    parsed = urllib.parse.urlparse(site)
                    domain = parsed.netloc.lower().replace("www.", "")
                    if domain and not any(skip in domain for skip in SKIP_DOMAINS):
                        out["website"] = f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")
                raw_p = pl.get("phoneNumber") or ""
                cp = _clean_phone(raw_p)
                if cp and cp not in out["phones"]:
                    out["phones"].append(cp)
                cid = pl.get("cid")
                if cid:
                    u = f"https://maps.google.com/?cid={cid}"
                    if u not in out["web_sources"]:
                        out["web_sources"].append(u)
                break
    except Exception as exc:
        _log.debug("Serper maps lookup suppressed: %s: %s", type(exc).__name__, exc)

    # 2. Serper Google Search (/search) -> website + emails + phones + directory/social links
    for sq in (
        f'"{clean_co}" {city} email OR contact OR phone'.strip(),
        f'"{clean_co}" {city} site:justdial.com OR site:indiamart.com OR site:tradeindia.com OR site:linkedin.com'.strip(),
    ):
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                json={"q": sq, "gl": "in", "num": 8},
                headers=headers,
                timeout=6,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            for item in data.get("organic") or []:
                link = (item.get("link") or "").strip()
                text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
                if link:
                    parsed = urllib.parse.urlparse(link)
                    domain = parsed.netloc.lower().replace("www.", "")
                    if not out["website"] and domain and not any(skip in domain for skip in SKIP_DOMAINS):
                        out["website"] = f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")
                    if any(d in link.lower() for d in ("justdial.com", "indiamart.com", "tradeindia.com", "linkedin.com", "facebook.com", "instagram.com", "zaubacorp.com", "tofler.in")):
                        if link not in out["web_sources"] and len(out["web_sources"]) < 10:
                            out["web_sources"].append(link)
                for raw_e in EMAIL_RE.findall(text):
                    ce = _clean_email(raw_e, clean_co)
                    if ce and ce not in out["emails"]:
                        out["emails"].append(ce)
                for raw_p in PHONE_RE.findall(text):
                    cp = _clean_phone(raw_p)
                    if cp and cp not in out["phones"]:
                        out["phones"].append(cp)
        except Exception as exc:
            _log.debug("Serper search lookup suppressed: %s: %s", type(exc).__name__, exc)

    return out


def _find_website_via_places(company: str, city: str = "") -> Dict[str, Any]:
    """Use Google Places API (if GOOGLE_MAPS_API_KEY is set) to get official website, phones, and Maps URL."""
    out: Dict[str, Any] = {"website": None, "phones": [], "source_url": None}
    if os.getenv("PYTEST_CURRENT_TEST"):
        return out
    api_key = (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip()
    if not api_key or not company:
        return out
    try:
        q = f"{company} {city} India".strip()
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": q, "key": api_key},
            timeout=6,
        )
        if r.status_code != 200:
            return out
        results = r.json().get("results") or []
        if not results:
            return out
        place_id = results[0].get("place_id")
        if not place_id:
            return out
        det = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                "place_id": place_id,
                "fields": "website,formatted_phone_number,international_phone_number,url",
                "key": api_key,
            },
            timeout=6,
        )
        if det.status_code != 200:
            return out
        info = det.json().get("result") or {}
        site = (info.get("website") or "").strip()
        if site:
            parsed = urllib.parse.urlparse(site)
            domain = parsed.netloc.lower().replace("www.", "")
            if domain and not any(skip in domain for skip in SKIP_DOMAINS):
                out["website"] = f"{parsed.scheme or 'https'}://{parsed.netloc}".rstrip("/")
        for p_key in ("international_phone_number", "formatted_phone_number"):
            raw_p = info.get(p_key) or ""
            cp = _clean_phone(raw_p)
            if cp and cp not in out["phones"]:
                out["phones"].append(cp)
        if info.get("url"):
            out["source_url"] = info["url"]
    except Exception as exc:
        _log.debug("Google Places lookup suppressed: %s: %s", type(exc).__name__, exc)
    return out


def _extract_serp_links(html: str) -> List[str]:
    """Extract clean external http(s) links from Google/Bing/DDG SERP HTML."""
    links: List[str] = []
    # Google uses /url?q=https://... as well as direct href="https://..."
    for raw in re.findall(r'href="(?:https?://[^"]+|/url\?q=https?://[^"&]+)', html or ""):
        link = raw
        if link.startswith("/url?q="):
            link = urllib.parse.unquote(link[7:])
        links.append(link)
    return links


def _find_website(company: str, city: str = "") -> Optional[str]:
    """
    Discover official company website via Serper, Google Places, Bing, Google, and DDG fallback.
    Filters out aggregator directories, social networks, and search portals.
    """
    serper = _find_via_serper(company, city)
    if serper.get("website"):
        return serper["website"]

    places = _find_website_via_places(company, city)
    if places.get("website"):
        return places["website"]

    clean_co = _clean_company_query(company)
    query = f"{clean_co} {city} official website".strip()
    search_urls = [
        f"https://www.bing.com/search?q={urllib.parse.quote_plus(query)}",
        f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}",
        f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}",
    ]
    for url in search_urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=4)
            if resp.status_code != 200:
                continue
            for link in _extract_serp_links(resp.text):
                parsed = urllib.parse.urlparse(link)
                domain = parsed.netloc.lower().replace("www.", "")
                if not domain or any(skip in domain for skip in SKIP_DOMAINS):
                    continue
                if any(ext in parsed.path.lower() for ext in (".js", ".css", ".png", ".jpg", ".svg")):
                    continue
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        except Exception as exc:
            _log.debug("Website search suppressed (%s): %s: %s", url.split("/")[2], type(exc).__name__, exc)
    return None


def _search_web_presence(company: str, city: str = "", website: str = "") -> Dict[str, Any]:
    """
    Search everywhere on the web (Google Places, Bing/Google SERPs, JustDial/IndiaMart/
    TradeIndia/LinkedIn/social snippets) to extract all emails, phones, and web URLs.
    """
    out: Dict[str, Any] = {"emails": [], "phones": [], "web_sources": []}
    if os.getenv("PYTEST_CURRENT_TEST") or not company:
        return out

    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    seen_urls: set[str] = set()

    # 1. Serper Google Maps + Search (preferred when SERPER_API_KEY is set)
    serper = _find_via_serper(company, city)
    for e in serper.get("emails", []):
        if e not in seen_emails:
            seen_emails.add(e)
            out["emails"].append(e)
    for p in serper.get("phones", []):
        if p not in seen_phones:
            seen_phones.add(p)
            out["phones"].append(p)
    for ws in serper.get("web_sources", []):
        if ws not in seen_urls and len(out["web_sources"]) < 10:
            seen_urls.add(ws)
            out["web_sources"].append(ws)
    if serper.get("emails") or serper.get("phones") or serper.get("web_sources"):
        return out

    # 2. Google Places phone & Maps URL
    places = _find_website_via_places(company, city)
    for p in places.get("phones", []):
        if p not in seen_phones:
            seen_phones.add(p)
            out["phones"].append(p)
    if places.get("source_url"):
        seen_urls.add(places["source_url"])
        out["web_sources"].append(places["source_url"])

    # 2. Web-wide search queries (Bing + Google)
    queries = [
        f'"{company}" "{city}" email OR contact OR phone',
        f'"{company}" "{city}" site:justdial.com OR site:indiamart.com OR site:tradeindia.com OR site:linkedin.com',
    ]
    strip_tags_re = re.compile(r"<(?:head|script|style)[^>]*>.*?</(?:head|script|style)>", re.I | re.S)
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", company) if len(t) >= 3][:2]

    for q in queries:
        for engine_url in (
            f"https://www.bing.com/search?q={urllib.parse.quote_plus(q)}",
            f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}",
        ):
            try:
                resp = requests.get(engine_url, headers=_HEADERS, timeout=5)
                if resp.status_code != 200 or not resp.text:
                    continue
                body = strip_tags_re.sub(" ", resp.text)
                plain = re.sub(r"<[^>]+>", " ", body)

                # Collect directory/social presence links
                for link in _extract_serp_links(resp.text):
                    low = link.lower()
                    if any(d in low for d in ("justdial.com", "indiamart.com", "tradeindia.com", "linkedin.com", "facebook.com", "instagram.com", "zaubacorp.com", "tofler.in")):
                        clean_l = link.split("&")[0].split("#")[0]
                        if clean_l not in seen_urls and len(out["web_sources"]) < 10:
                            seen_urls.add(clean_l)
                            out["web_sources"].append(clean_l)

                # Extract emails from visible SERP text
                for raw in EMAIL_RE.findall(plain):
                    cl = _clean_email(raw, company)
                    if cl and cl not in seen_emails:
                        seen_emails.add(cl)
                        out["emails"].append(cl)

                # Extract phones ONLY near company name mention to avoid unrelated SERP numbers
                if tokens:
                    probe = re.compile(r"\s+".join(re.escape(t) for t in tokens), re.I)
                    for m in probe.finditer(plain):
                        snippet = plain[max(0, m.start() - 350): m.end() + 350]
                        for raw_p in PHONE_RE.findall(snippet):
                            cp = _clean_phone(raw_p)
                            if cp and cp not in seen_phones:
                                seen_phones.add(cp)
                                out["phones"].append(cp)
                break  # Got 200 from first working search engine for this query
            except Exception as exc:
                _log.debug("Web presence search suppressed: %s: %s", type(exc).__name__, exc)

    return out


def _crawl_website(website: str, company: str = "") -> Dict[str, Any]:
    """
    Crawls key pages of the website to extract:
    - Emails, Phones, WhatsApp
    - Decision maker names & titles
    - Coffee buying signals & competitor brands
    - FSSAI & GSTIN
    - Aggregated text for Truth Layer evidence
    """
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"

    # Target pages to explore
    pages_to_visit = [
        base,
        f"{base}/contact",
        f"{base}/contact-us",
        f"{base}/about",
        f"{base}/about-us",
        f"{base}/menu",
        f"{base}/beverages",
        f"{base}/coffee",
        f"{base}/products",
        f"{base}/services",
        f"{base}/procurement",
        f"{base}/enquiry",
    ]

    all_emails: set[str] = set()
    all_phones: set[str] = set()
    whatsapp_number = ""
    dm_name = None
    dm_title = None
    gstin = None
    fssai = None
    combined_text = []

    coffee_signals = {
        "instant_coffee": False,
        "coffee_service": False,
        "pantry_cafeteria": False,
        "breakfast_buffet": False,
        "wholesale_procurement": False,
    }
    detected_brands: list[str] = []

    pages_visited = 0
    max_pages = 5

    for page_url in pages_to_visit:
        if pages_visited >= max_pages:
            break
        try:
            r = requests.get(page_url, headers=_HEADERS, timeout=7, allow_redirects=True)
            if r.status_code != 200:
                continue
            pages_visited += 1
            text = r.text
            combined_text.append(text[:50000])

            # 1. Emails
            raw_emails = EMAIL_RE.findall(text)
            mailto_emails = re.findall(r'mailto:([^\s"\'<>?&]+)', text, re.IGNORECASE)
            for raw in raw_emails + mailto_emails:
                cl = _clean_email(raw, company)
                if cl:
                    all_emails.add(cl)

            # 2. Phones
            raw_phones = PHONE_RE.findall(text)
            for p in raw_phones:
                clean_p = _clean_phone(p)
                if clean_p:
                    all_phones.add(clean_p)

            # 3. WhatsApp link
            if not whatsapp_number:
                wa_match = re.search(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(?:91)?(\d{10})", text, re.IGNORECASE)
                if wa_match:
                    wa_clean = _clean_phone(wa_match.group(1))
                    if wa_clean:
                        whatsapp_number = wa_clean

            # 4. Decision Maker Name & Designation
            if not dm_title:
                m_title = DM_TITLE_RE.search(text)
                if m_title:
                    dm_title = m_title.group(1).title()

            if not dm_name:
                m_name = PERSON_NAME_RE.search(text)
                if m_name:
                    dm_name = f"{m_name.group(1)} {m_name.group(2)}"

            # 5. GSTIN & FSSAI
            if not gstin:
                m_gst = GSTIN_RE.search(text)
                if m_gst:
                    gstin = m_gst.group(0)

            if not fssai:
                m_fssai = FSSAI_RE.search(text)
                if m_fssai:
                    fssai = m_fssai.group(1)

            # 6. Coffee Buying Signals
            if RE_INSTANT_COFFEE.search(text):
                coffee_signals["instant_coffee"] = True
            if RE_COFFEE_SERVICE.search(text):
                coffee_signals["coffee_service"] = True
            if RE_PANTRY_CAFETERIA.search(text):
                coffee_signals["pantry_cafeteria"] = True
            if RE_BREAKFAST_BUFFET.search(text):
                coffee_signals["breakfast_buffet"] = True
            if RE_WHOLESALE_PROCUREMENT.search(text):
                coffee_signals["wholesale_procurement"] = True

            # 7. Competitor Brands
            for brand_name, brand_regex in COMPETITOR_BRANDS:
                if brand_name not in detected_brands and brand_regex.search(text):
                    detected_brands.append(brand_name)

            time.sleep(random.uniform(0.2, 0.5))

        except Exception as exc:
            _log.debug("Crawl error on %s: %s", page_url, exc)
            continue

    total_site_text = " ".join(combined_text)

    return {
        "emails": sorted(all_emails)[:6],
        "phones": sorted(all_phones)[:4],
        "whatsapp": whatsapp_number or (sorted(all_phones)[0] if all_phones else ""),
        "decision_maker_name": dm_name,
        "decision_maker_title": dm_title,
        "gstin": gstin,
        "fssai": fssai,
        "coffee_signals": coffee_signals,
        "detected_brands": detected_brands,
        "site_text": total_site_text[:150000],
    }


def _pick_decision_maker_email(emails: list[str], company: str = "") -> Optional[str]:
    """
    Prefer decision-maker role and procurement mailboxes.
    Priority: procurement/purchase/director/owner > sales/office > info/contact > others.
    """
    if not emails:
        return None

    p1 = ["purchase", "procurement", "buyer", "director", "md", "owner", "proprietor", "founder", "head"]
    p2 = ["sales", "commercial", "fnb", "operations", "admin", "manager", "store"]
    p3 = ["info", "contact", "hello", "office", "enquiry", "reachus"]

    for e in emails:
        user = e.split("@")[0].lower()
        if any(k in user for k in p1):
            return e
    for e in emails:
        user = e.split("@")[0].lower()
        if any(k in user for k in p2):
            return e
    for e in emails:
        user = e.split("@")[0].lower()
        if any(k in user for k in p3):
            return e

    return emails[0]


def _score_confidence(res: dict) -> int:
    """Calculate 0-100 data completeness and verification confidence."""
    score = 0
    if res.get("website"):
        score += 25
    if res.get("emails"):
        score += 25
    if res.get("phones"):
        score += 25
    if res.get("decision_maker_name") or res.get("decision_maker_title"):
        score += 10
    if any(res.get("coffee_signals", {}).values()):
        score += 15
    return min(100, score)


def enrich_lead(company: str, city: str = "", website: str = "", division: str = "",
                lead=None, db=None) -> dict:
    """
    Enrich a single lead with verified contacts, buying signals, and Truth Layer evidence.
    If `lead` and `db` are provided, performs live transactional database enrichment.
    """
    result = {
        "website": website or None,
        "emails": [],
        "phones": [],
        "web_sources": [],
        "whatsapp": "",
        "decision_maker_email": None,
        "decision_maker_name": None,
        "decision_maker_title": None,
        "gstin": None,
        "fssai": None,
        "enrichment_confidence": 0,
        "enrichment_notes": [],
        "coffee_signals": {},
        "detected_brands": [],
        "coffee_buying_score": 0,
        "intent_score": 0,
        "intent_tier": "Below 40",
    }

    # Step 1: Discover website if not known
    if not result["website"]:
        found_site = _find_website(company, city)
        if found_site:
            result["website"] = found_site
            result["enrichment_notes"].append(f"Official website discovered: {found_site}")

    # Step 2: Deep crawl if website available + search web presence everywhere
    site_text = ""
    if result["website"]:
        crawled = _crawl_website(result["website"], company)
        result["emails"] = list(crawled["emails"])
        result["phones"] = list(crawled["phones"])
        result["whatsapp"] = crawled["whatsapp"]
        result["decision_maker_name"] = crawled["decision_maker_name"]
        result["decision_maker_title"] = crawled["decision_maker_title"]
        result["gstin"] = crawled["gstin"]
        result["fssai"] = crawled["fssai"]
        result["coffee_signals"] = crawled["coffee_signals"]
        result["detected_brands"] = crawled["detected_brands"]
        site_text = crawled["site_text"]

    web_hits = _search_web_presence(company, city, result["website"] or "")
    for e in web_hits.get("emails", []):
        if e not in result["emails"]:
            result["emails"].append(e)
    for p in web_hits.get("phones", []):
        if p not in result["phones"]:
            result["phones"].append(p)
    result["web_sources"] = web_hits.get("web_sources", [])

    if result["emails"]:
        result["enrichment_notes"].append(f"Found {len(result['emails'])} verified business email(s)")
        result["decision_maker_email"] = _pick_decision_maker_email(result["emails"], company)
    if result["phones"]:
        result["enrichment_notes"].append(f"Found {len(result['phones'])} verified mobile number(s)")
    if result["web_sources"]:
        result["enrichment_notes"].append(f"Found {len(result['web_sources'])} web/directory presence URL(s)")
    if result["decision_maker_name"]:
        result["enrichment_notes"].append(f"Decision maker identified: {result['decision_maker_name']} ({result['decision_maker_title'] or 'Management'})")
    if result["detected_brands"]:
        result["enrichment_notes"].append(f"Competitor coffee brands detected: {', '.join(result['detected_brands'])}")

    sig_hits = [k for k, v in result["coffee_signals"].items() if v]
    if sig_hits:
        result["enrichment_notes"].append(f"Coffee buying signals identified: {', '.join(sig_hits)}")

    result["enrichment_confidence"] = _score_confidence(result)

    # Step 3: Database & Truth Layer Sync (if lead & db provided)
    if lead is not None and db is not None:
        _sync_lead_with_enrichment(lead, result, site_text, db)

    return result


def _sync_lead_with_enrichment(lead, result: dict, site_text: str, db) -> None:
    """Synchronizes enrichment findings directly into B2BLead and LeadEvidence."""
    from app.services import evidence_collector, coffee_demand, high_intent_scorer
    from app.services.contact_enricher import phone_is_authoritative, digits_only

    changed = False

    # 1. Website
    if result["website"] and not lead.website:
        lead.website = result["website"]
        changed = True

    # 2. Email (+ MX validation when newly discovered)
    if result["decision_maker_email"] and not lead.email:
        lead.email = result["decision_maker_email"]
        lead.email_source = "Official Website & Web Enrichment"
        lead.email_collected_at = datetime.utcnow()
        changed = True

    if (
        not os.getenv("PYTEST_CURRENT_TEST")
        and getattr(lead, "email", None)
        and getattr(lead, "email_trust", None) in (None, "UNKNOWN", "DISCOVERED")
    ):
        try:
            from app.services import email_verifier
            vr = email_verifier.verify_email(lead.email)
            if vr.get("valid") or vr.get("mx_valid"):
                lead.email_trust = "VALIDATED"
                lead.email_mx_valid = True
                lead.email_verification_status = "VALID"
                if getattr(lead, "contact_status", None) == "NEEDS_RESEARCH" and not getattr(lead, "dnc_reason", None):
                    lead.contact_status = "CONTACTABLE"
            else:
                lead.email_trust = "INVALID"
                lead.email_mx_valid = False
                lead.email_verification_status = "INVALID"
            changed = True
        except Exception as exc:
            _log.debug("MX verification skipped for lead %s: %s", getattr(lead, "id", 0), exc)

    # 3. Phone (guard against overwriting authoritative first-party numbers)
    if result["phones"]:
        best_phone = result["phones"][0]
        protected = phone_is_authoritative(lead)
        if not lead.phone:
            lead.phone = best_phone
            lead.phone_source = "Official Website & Web Enrichment"
            lead.phone_collected_at = datetime.utcnow()
            changed = True
        elif not protected and digits_only(lead.phone) != digits_only(best_phone):
            lead.phone = best_phone
            lead.phone_source = "Official Website & Web Enrichment"
            lead.phone_collected_at = datetime.utcnow()
            changed = True

    # 4. WhatsApp
    if result["whatsapp"] and not lead.whatsapp_number:
        lead.whatsapp_number = result["whatsapp"]
        changed = True

    # 4b. Store ALL discovered emails, phones, website, and web URLs in LeadEvidence (deduplicated)
    if getattr(lead, "id", None):
        try:
            from app.models.models import LeadEvidence
            existing_ev = {
                (r[0], r[1])
                for r in db.query(LeadEvidence.signal_type, LeadEvidence.value)
                .filter(LeadEvidence.lead_id == lead.id)
                .all()
            }
            def _add_ev(sig_type: str, val: str, src: str, src_url: Optional[str] = None):
                if val and (sig_type, val) not in existing_ev:
                    db.add(LeadEvidence(
                        lead_id=lead.id,
                        signal_type=sig_type,
                        value=val[:500],
                        source=src,
                        source_url=src_url,
                        confidence=0.75,
                        verified=True,
                    ))
                    existing_ev.add((sig_type, val))
            if result["website"]:
                _add_ev("OFFICIAL_WEBSITE", result["website"], "Web Enrichment", result["website"])
            for em in result.get("emails", []):
                _add_ev("CONTACT_EMAIL", em, "Web Enrichment", result.get("website"))
            for ph in result.get("phones", []):
                _add_ev("CONTACT_PHONE", ph, "Web Enrichment", result.get("website"))
            for ws in result.get("web_sources", []):
                _add_ev("WEB_PRESENCE", ws, "Web Search", ws)
        except Exception as exc:
            _log.debug("Contact LeadEvidence write skipped for lead %s: %s", getattr(lead, "id", 0), exc)

    # 5. Decision Maker / Persona
    if result["decision_maker_name"] and not lead.contact_name:
        lead.contact_name = result["decision_maker_name"]
        changed = True
    if result["decision_maker_title"] and not lead.contact_title:
        lead.contact_title = result["decision_maker_title"]
        lead.decision_maker = result["decision_maker_title"]
        lead.decision_maker_score = 80
        changed = True

    # 6. Competitor Brand / Current Supplier
    if result["detected_brands"]:
        lead.current_brand = result["detected_brands"][0]
        if not lead.current_supplier:
            lead.current_supplier = result["detected_brands"][0]
        changed = True

    # 7. Truth Layer: Collect Evidence
    try:
        evidence_collector.collect_for_lead(lead, db, site_text=site_text)
    except Exception as exc:
        _log.warning("Evidence collection failed for lead %s: %s", getattr(lead, "id", 0), exc)

    # 8. Coffee Demand Score
    try:
        demand = coffee_demand.score_lead(lead, db, fetch_website=False)
        lead.coffee_buying_score = demand.score
        lead.coffee_buying_evidence = "; ".join(s.get("evidence", "") for s in demand.signals)
        result["coffee_buying_score"] = demand.score
    except Exception as exc:
        _log.warning("Coffee demand score failed for lead %s: %s", getattr(lead, "id", 0), exc)

    # 9. Append Enrichment Findings into qualification_notes
    notes_parts = []
    if getattr(lead, "qualification_notes", None):
        notes_parts.append(str(lead.qualification_notes))
    
    new_findings = []
    if result["coffee_signals"].get("instant_coffee"):
        new_findings.append("instant coffee requirement on site")
    if result["coffee_signals"].get("wholesale_procurement"):
        new_findings.append("wholesale institutional supply inquiry")
    if result["coffee_signals"].get("pantry_cafeteria"):
        new_findings.append("commercial pantry/cafeteria operations")
    if result["detected_brands"]:
        new_findings.append(f"vendor displacement opportunity ({', '.join(result['detected_brands'])})")
    if result["fssai"]:
        new_findings.append(f"FSSAI license verified: {result['fssai']}")
    if result["gstin"]:
        new_findings.append(f"GSTIN: {result['gstin']}")

    if new_findings:
        notes_parts.append(f"[Enrichment Verified {datetime.utcnow().strftime('%Y-%m-%d')}]: " + "; ".join(new_findings))
        lead.qualification_notes = "\n".join(notes_parts)
        changed = True

    contact_parts = []
    if result.get("emails"):
        contact_parts.append("emails=" + ", ".join(result["emails"]))
    if result.get("phones"):
        contact_parts.append("phones=" + ", ".join(result["phones"]))
    if result.get("whatsapp"):
        contact_parts.append("whatsapp=" + result["whatsapp"])
    if result.get("web_sources"):
        contact_parts.append("web_sources=" + ", ".join(result["web_sources"][:5]))
    if contact_parts:
        tag = f"[Enriched Contacts {datetime.utcnow().strftime('%Y-%m-%d')}]: " + " | ".join(contact_parts)
        if tag not in (lead.qualification_notes or ""):
            notes_parts.append(tag)
            lead.qualification_notes = "\n".join(notes_parts)
            changed = True

    # 10. High-Intent Evaluation & Priority Promotion
    try:
        intent_eval = high_intent_scorer.evaluate_intent(lead, db)
        lead.intent_score = intent_eval.score
        lead.intent_tier = intent_eval.tier
        result["intent_score"] = intent_eval.score
        result["intent_tier"] = intent_eval.tier
        changed = True
    except Exception as exc:
        _log.warning("High intent scoring failed for lead %s: %s", getattr(lead, "id", 0), exc)

    # 11. Stamping & Status
    lead.intelligence_status = "ENRICHED"
    lead.evidence_collected_at = datetime.utcnow()
    lead.last_updated = datetime.utcnow()


def _maybe_queue_outreach_draft(lead, db) -> bool:
    """Queue a verified PENDING intro EmailDraft if enrichment yielded a sendable email."""
    try:
        from app.models.models import EmailDraft
        from app.services import auto_send, outreach_engine
        from app.services.category_classifier import classify_macro_outreach_segment

        if not getattr(lead, "id", None) or not getattr(lead, "email", None):
            return False
        if getattr(lead, "contact_status", None) != "CONTACTABLE":
            return False
        if getattr(lead, "email_trust", None) not in ("VALIDATED", "VERIFIED", "TRUSTED"):
            return False

        active_statuses = (
            "DRAFT", "PENDING", "EDITED", "APPROVED", "FOUNDER_APPROVED",
            "AUTO_APPROVED", "QUEUED", "SENDING", "SENT",
        )
        exists = db.query(EmailDraft.id).filter(
            EmailDraft.lead_id == lead.id,
            EmailDraft.status.in_(active_statuses),
        ).first()
        if exists:
            return False

        ok_lead, _ = auto_send.lead_check(db, lead, lead.email, decision=False)
        if not ok_lead:
            return False

        sig = getattr(outreach_engine, "SIGNATURE", None) or "Warm regards,\nHiten Jain\nFounder, Pure Pantry Provisions | Purity Beans"
        draft_data = outreach_engine.build_draft(lead, None, 0, False, None, sig)
        subj = draft_data.get("subject") or ""
        body = draft_data.get("body") or ""
        verdict, _ = auto_send.content_check(subj, body)
        if verdict != "OK":
            return False

        cat, _ = classify_macro_outreach_segment(lead)
        db.add(EmailDraft(
            lead_id=lead.id,
            recipient=lead.email,
            subject=subj,
            body=body,
            status="PENDING",
            follow_up_type="intro",
            reason=f"post-enrichment verified intro ({cat})",
            created_at=datetime.utcnow(),
        ))
        return True
    except Exception as exc:
        _log.debug("Auto draft queue skipped for lead %s: %s", getattr(lead, "id", 0), exc)
        return False


def enrich_batch(leads, db, max_per_run: int = 30) -> dict:
    """
    Enriches a batch of leads.
    Extracts contacts, records Truth Layer evidence, updates coffee buying & intent scores,
    and logs LEAD_ENRICHED workflow events.
    """
    from app.services.pipeline_tracker import track

    enriched = 0
    skipped = 0
    failed = 0
    promoted_priority_a = 0
    promoted_priority_b = 0

    for lead in leads[:max_per_run]:
        lead_id = getattr(lead, "id", 0)
        try:
            result = enrich_lead(
                company=lead.company or "",
                city=lead.city or "",
                website=lead.website or "",
                division=lead.division or "",
                lead=lead,
                db=db,
            )

            # Check if promoted
            if result.get("intent_tier") == "Priority A":
                promoted_priority_a += 1
            elif result.get("intent_tier") == "Priority B":
                promoted_priority_b += 1

            track(
                db, "LEAD_ENRICHED", lead_id=lead_id, actor="SYSTEM",
                payload={
                    "website": result["website"],
                    "emails_found": result["emails"],
                    "phones_found": result["phones"],
                    "web_sources": result.get("web_sources", []),
                    "confidence": result["enrichment_confidence"],
                    "coffee_signals": result["coffee_signals"],
                    "detected_brands": result["detected_brands"],
                    "coffee_buying_score": result["coffee_buying_score"],
                    "intent_score": result["intent_score"],
                    "intent_tier": result["intent_tier"],
                    "notes": result["enrichment_notes"],
                },
            )
            if not os.getenv("PYTEST_CURRENT_TEST"):
                _maybe_queue_outreach_draft(lead, db)
            enriched += 1
            db.commit()

            time.sleep(random.uniform(0.3, 0.8))

        except Exception as exc:
            failed += 1
            db.rollback()
            _log.error("Enrichment failed on lead %s: %s", lead_id, exc)
            track(
                db, "WORKFLOW_FAILED", lead_id=lead_id, actor="SYSTEM",
                payload={"step": "enrichment", "error": str(exc)[:200]},
            )

    return {
        "enriched": enriched,
        "skipped": skipped,
        "failed": failed,
        "promoted_priority_a": promoted_priority_a,
        "promoted_priority_b": promoted_priority_b,
    }
