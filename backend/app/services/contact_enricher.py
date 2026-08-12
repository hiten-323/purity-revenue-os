"""
Contact Enrichment Service
Searches JustDial, Sulekha, IndiaMart, and Google Business Profile
to confirm/find phone + WhatsApp numbers for a given company.
"""

from __future__ import annotations
import re
import httpx
import concurrent.futures
from typing import Optional
import logging

_log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8"}

_PHONE_RE = re.compile(r'(\+91[\s\-]?\d{5}[\s\-]?\d{5}|\b[6-9]\d{9}\b)')
_EMAIL_RE = re.compile(r'[\w.\-+]+@[\w.\-]+\.[a-zA-Z]{2,6}')
_BAD_EMAIL_DOMAINS = {
    'justdial','sulekha','indiamart','tradeindia','google','sentry','schema',
    'example','w3.org','gstatic','facebook','instagram','twitter','linkedin',
    'cloudflare','amazonaws','jquery','bootstrap',
}

def _clean_emails(raw: list[str]) -> list[str]:
    seen, out = set(), []
    for e in raw:
        e = e.lower().strip('.,;')
        if any(b in e for b in _BAD_EMAIL_DOMAINS):
            continue
        if e in seen or len(e) > 80:
            continue
        seen.add(e)
        out.append(e)
    return out


def _norm(p: str) -> str:
    """Normalize to +91XXXXXXXXXX."""
    digits = re.sub(r'\D', '', p)
    if digits.startswith('91') and len(digits) == 12:
        return f"+91-{digits[2:7]}-{digits[7:]}"
    if len(digits) == 10 and digits[0] in '6789':
        return f"+91-{digits[:5]}-{digits[5:]}"
    return p


def _phones_near_company(html: str, company: str, window: int = 600) -> list[str]:
    """
    Extract phone numbers ONLY from text near a mention of the company name.
    Search-result and directory pages list dozens of unrelated businesses —
    a number is attributable to this company only if it appears close to the
    company's name. If the name never appears on the page, return nothing.
    """
    if not html or not company:
        return []
    # Match on the first 2 significant name tokens (handles suffix noise
    # like "Pvt Ltd" / "& Sons" in listings)
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", company) if len(t) >= 3][:2]
    if not tokens:
        return []
    probe = re.compile(r"\s*".join(re.escape(t) for t in tokens), re.IGNORECASE)
    phones: list[str] = []
    for m in probe.finditer(html):
        start = max(0, m.start() - window)
        segment = html[start: m.end() + window]
        phones.extend(_PHONE_RE.findall(segment))
    return list(dict.fromkeys(phones))


def _search_justdial(company: str, city: str) -> dict:
    """Search JustDial for the company and extract phone numbers."""
    slug = company.lower().replace(' ', '-').replace('&', 'and')
    city_slug = city.lower().replace(' ', '-')
    urls = [
        f"https://www.justdial.com/{city}/{slug}/nct-10215973",
        f"https://www.justdial.com/{city_slug}/{slug}",
        f"https://www.justdial.com/search?q={company.replace(' ', '+')}&where={city.replace(' ', '+')}",
    ]
    phones = []
    email = ""
    for url in urls:
        try:
            with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
                html = c.get(url).text
            found = _phones_near_company(html, company)
            phones.extend(found)
            em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
            if em and not email:
                email = [e for e in em if not any(x in e for x in ['justdial', 'sentry', 'example'])][0] if em else ""
            if phones:
                break
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "JustDial"}


def _search_sulekha(company: str, city: str) -> dict:
    """Search Sulekha for the company."""
    q = company.replace(' ', '+')
    c_slug = city.lower().replace(' ', '-')
    url = f"https://www.sulekha.com/{c_slug}/{q.replace('+', '-').lower()}-dealers"
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get(url).text
        phones = _phones_near_company(html, company)[:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        email = next((e for e in em if 'sulekha' not in e and 'example' not in e), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "Sulekha"}


def _search_indiamart(company: str, city: str) -> dict:
    """Search IndiaMart directory for the company."""
    url = "https://dir.indiamart.com/search.mp"
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12, headers={**_HEADERS, "Referer": "https://www.indiamart.com/"}, follow_redirects=True) as c:
            html = c.get(url, params={"ss": company, "CatLGid": city}).text
        phones = _phones_near_company(html, company)[:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        email = next((e for e in em if 'indiamart' not in e and 'example' not in e), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "IndiaMart"}


def _search_tradeindia(company: str, city: str) -> dict:
    """Search TradeIndia directory for the company."""
    q = company.replace(' ', '+')
    url = f"https://www.tradeindia.com/search/?search_string={q}&city={city}"
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get(url).text
        phones = _phones_near_company(html, company)[:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        email = next((e for e in em if 'tradeindia' not in e and 'example' not in e), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "TradeIndia"}


def _search_google_business(company: str, city: str) -> dict:
    """Search Google for the business contact info."""
    query = f"{company} {city} phone number contact"
    url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
    phones = []
    email = ""
    try:
        headers = {**_HEADERS, "Accept": "text/html,application/xhtml+xml"}
        with httpx.Client(timeout=12, headers=headers, follow_redirects=True) as c:
            html = c.get(url).text
        phones = _phones_near_company(html, company)[:6]
        # Filter out obviously wrong numbers (Google's own, etc.)
        phones = [p for p in phones if not p.startswith('+1') and '1800' not in p][:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        bad = {'google', 'example', 'schema', 'w3.org', 'sentry', 'gstatic'}
        email = next((e for e in em if not any(b in e for b in bad)), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "Google"}


def _search_duckduckgo(company: str, city: str) -> dict:
    """Wide-web search via DuckDuckGo HTML (covers pages beyond directories)."""
    query = f'"{company}" {city} contact phone email'
    url = "https://html.duckduckgo.com/html/"
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get(url, params={"q": query}).text
        phones = _phones_near_company(html, company)[:6]
        phones = [p for p in phones if not p.startswith('+1') and '1800' not in p][:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        bad = {'duckduckgo', 'example', 'schema', 'w3.org', 'sentry', 'gstatic', 'google'}
        email = next((e for e in em if not any(b in e for b in bad)), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "WebSearch"}


def _search_bing(company: str, city: str) -> dict:
    """Wide-web search via Bing (second engine — cross-confirms numbers)."""
    query = f"{company} {city} phone contact"
    url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get(url).text
        phones = _phones_near_company(html, company)[:6]
        phones = [p for p in phones if not p.startswith('+1') and '1800' not in p][:4]
        em = re.findall(r'[\w.\-]+@[\w.\-]+\.[a-z]{2,6}', html)
        bad = {'bing', 'microsoft', 'example', 'schema', 'w3.org', 'sentry'}
        email = next((e for e in em if not any(b in e for b in bad)), "")
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:4], "email": email, "source": "Bing"}


def _search_google_places(company: str, city: str) -> dict:
    """
    Google Places API — authoritative source. Returns the phone number the
    business itself registered with Google. Uses the existing
    GOOGLE_MAPS_API_KEY; skipped silently if not configured.
    """
    import os
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key or "your_" in key.lower():
        return {"phones": [], "email": "", "source": "GooglePlaces"}
    phones = []
    try:
        with httpx.Client(timeout=12) as c:
            find = c.get(
                "https://maps.googleapis.com/maps/api/place/findplacefromtext/json",
                params={"input": f"{company} {city}", "inputtype": "textquery",
                        "fields": "place_id,name", "key": key},
            ).json()
            candidates = find.get("candidates", [])
            # Only trust the match if the returned name resembles the company
            tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", company) if len(t) >= 3][:2]
            for cand in candidates[:2]:
                name = (cand.get("name") or "").lower()
                if tokens and not any(t in name for t in tokens):
                    continue
                details = c.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={"place_id": cand["place_id"],
                            "fields": "formatted_phone_number,international_phone_number,website",
                            "key": key},
                ).json().get("result", {})
                for f in ("international_phone_number", "formatted_phone_number"):
                    if details.get(f):
                        phones.append(details[f])
                if phones:
                    break
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": list(dict.fromkeys(phones))[:2], "email": "", "source": "GooglePlaces"}


def _search_perplexity(company: str, city: str) -> dict:
    """
    Perplexity Sonar — AI web search with citations. Activates only when
    PERPLEXITY_API_KEY is set in .env; skipped silently otherwise.
    """
    import os, json
    key = os.getenv("PERPLEXITY_API_KEY", "")
    if not key or "your_" in key.lower():
        return {"phones": [], "email": "", "source": "Perplexity"}
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=25) as c:
            resp = c.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": "sonar",
                    "messages": [{
                        "role": "user",
                        "content": (
                            f'Find the official phone number and email address of the business '
                            f'"{company}" in {city}, India. Reply ONLY with JSON: '
                            f'{{"phone": "...", "email": "..."}}. '
                            f'Use empty strings if you cannot find VERIFIED information from '
                            f'real web sources. Never guess or invent contact details.'
                        ),
                    }],
                    "temperature": 0,
                },
            ).json()
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            if data.get("phone"):
                phones = _PHONE_RE.findall(data["phone"]) or ([data["phone"]] if re.sub(r"\D", "", data["phone"]) else [])
            if data.get("email") and "@" in data["email"]:
                email = data["email"].strip()
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": phones[:2], "email": email, "source": "Perplexity"}


def _search_brave(company: str, city: str) -> dict:
    """
    Brave Search API — structured JSON web results (free tier: 2,000/month).
    Activates only when BRAVE_API_KEY is set in .env; skipped silently otherwise.
    """
    import os
    key = os.getenv("BRAVE_API_KEY", "")
    if not key or "your_" in key.lower():
        return {"phones": [], "email": "", "source": "BraveSearch"}
    phones = []
    email = ""
    try:
        with httpx.Client(timeout=12) as c:
            resp = c.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": key, "Accept": "application/json"},
                params={"q": f'"{company}" {city} contact phone email', "country": "in", "count": 8},
            ).json()
        # Concatenate result titles+descriptions and attribute by name proximity
        blob = " ||| ".join(
            f"{r.get('title','')} {r.get('description','')} {r.get('url','')}"
            for r in resp.get("web", {}).get("results", [])
        )
        phones = _phones_near_company(blob, company, window=300)[:4]
        em = _EMAIL_RE.findall(blob)
        clean = _clean_emails(em)
        email = clean[0] if clean else ""
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return {"phones": phones, "email": email, "source": "BraveSearch"}


def _llm_judge(company: str, city: str, phone_sources: dict, emails: list) -> tuple | None:
    """
    Free LLM attribution — Cerebras first, local Ollama fallback if Cerebras
    is unreachable (see app.services.llm_client). Given the scraped candidate
    phones (with the sources that listed each) and candidate emails, ask the
    model to select only contacts it can confidently attribute to this exact
    business. Returns (phone, email) with empty strings for "cannot
    attribute", or None if unavailable.
    """
    import json
    from app.services.llm_client import complete as llm_complete

    if not phone_sources and not emails:
        return None
    candidates = [
        {"phone": p, "listed_by": srcs} for p, srcs in list(phone_sources.items())[:8]
    ]
    prompt = (
        f'Business: "{company}" in {city}, India.\n'
        f'Phone candidates scraped from web sources (each with the '
        f'directories/search engines that listed it near the business name):\n'
        f'{json.dumps(candidates)}\n'
        f'Email candidates: {json.dumps(emails[:6])}\n\n'
        f'Select the phone and email that most likely belong to this exact '
        f'business. Rules:\n'
        f'- Authoritative sources (GooglePlaces) outrank scraped pages.\n'
        f'- A number listed by 2+ independent sources is stronger.\n'
        f'- Reject candidates that look like helplines, portals, or unrelated '
        f'businesses (e.g. an email domain unrelated to the business name).\n'
        f'- If nothing is confidently attributable, use empty strings. '
        f'NEVER invent a contact.\n\n'
        f'Reply ONLY with JSON: {{"phone": "...", "email": "..."}}'
    )
    try:
        content, _source = llm_complete(prompt)
        if not content:
            return None
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        phone = _norm(data.get("phone", "") or "")
        email = (data.get("email", "") or "").strip().lower()
        return (phone if phone in phone_sources else "", email)
    except Exception:
        return None


def enrich_lead_contact(company: str, city: str, existing_phone: str = "",
                        website: str = "") -> dict:
    """
    Search 7 sources in parallel — 5 business directories plus 2 web-wide
    search engines — for the company's phone, WhatsApp and email.
    Returns the best confirmed phone, whatsapp_number, email, sources checked, and confidence.
    """
    scrapers = [
        (_search_justdial,        company, city),
        (_search_sulekha,         company, city),
        (_search_indiamart,       company, city),
        (_search_tradeindia,      company, city),
        (_search_google_business, company, city),
        (_search_duckduckgo,      company, city),
        (_search_bing,            company, city),
        (_search_google_places,   company, city),   # authoritative (API key)
        (_search_perplexity,      company, city),   # AI web search (needs PERPLEXITY_API_KEY)
        (_search_brave,           company, city),   # structured search (needs BRAVE_API_KEY)
    ]

    # The website we ALREADY HOLD is the single best source of a real address,
    # and it was being ignored: this function never accepted it, so
    # find_lead_email re-SEARCHED for a URL sitting in the database. When that
    # search missed, the one page guaranteed to publish a real contact was never
    # read. 170 businesses had a stored website and reported "no email found".
    _known_site = (website or "").strip()

    all_phones: list[str] = []
    all_emails: list[str] = []
    sources_checked: list[str] = []
    phone_sources: dict[str, list[str]] = {}   # normalized phone -> sources that listed it

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fn, co, ci): fn.__name__ for fn, co, ci in scrapers}
        for fut in concurrent.futures.as_completed(futures, timeout=25):
            try:
                res = fut.result()
                sources_checked.append(res["source"])
                all_phones.extend(res["phones"])
                for p in res["phones"]:
                    np = _norm(p)
                    phone_sources.setdefault(np, [])
                    if res["source"] not in phone_sources[np]:
                        phone_sources[np].append(res["source"])
                if res["email"]:
                    all_emails.append(res["email"])
            except Exception as _exc:
                # Swallowed on purpose — this path must not break the
                # caller — but never silently: a failure with no name is
                # how the category engine fell back for hours unnoticed.
                _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Normalize all found phones, dropping placeholder/junk digit-runs
    # (8888888888, 9999999999, 1234567890 …) that regexes pick up from
    # page markup — these must NEVER be confirmed as real numbers.
    def _is_junk(p: str) -> bool:
        digits = re.sub(r"\D", "", p)
        core = digits[-10:] if len(digits) >= 10 else digits
        if len(core) < 10:
            return True
        if len(set(core)) <= 2:
            return True
        if re.search(r"(12345|00000|11111)", core):
            return True
        # Indian mobiles start 6-9; landlines start with STD 0/city codes
        return core[0] not in "6789" and not digits.startswith("0")

    normalized = [_norm(p) for p in all_phones if p and not _is_junk(p)]

    # Count occurrences — a number seen across multiple sources is more trustworthy
    from collections import Counter
    freq = Counter(normalized)

    # Prefer the existing phone if it shows up in any source
    existing_norm = _norm(existing_phone) if existing_phone else ""
    if existing_norm and existing_norm in freq:
        confirmed = existing_norm
        confidence = "HIGH" if freq[existing_norm] >= 2 else "MEDIUM"
    elif freq:
        # Pick the most-seen number
        confirmed, count = freq.most_common(1)[0]
        confidence = "HIGH" if count >= 2 else "MEDIUM"
    elif existing_phone:
        confirmed = existing_norm or existing_phone
        confidence = "LOW"
    else:
        confirmed = ""
        confidence = "NONE"

    # WhatsApp: assume same as confirmed phone (>95% of Indian businesses use WhatsApp on business number)
    whatsapp = confirmed if confirmed else ""

    # Best email — reject portal noise AND foreign-country domains: an Indian
    # business in Abohar does not use a .com.au / .co.uk address; same-name
    # companies abroad are a real trap.
    _FOREIGN_TLD = re.compile(r"\.(au|uk|us|ca|nz|za|sg|ae|de|fr|cn|jp|ru|br|pk|bd|lk|np)$", re.I)
    # The company's own website, read before the directories are judged. It was
    # never consulted: enrich_lead_contact polled ten third-party directories and
    # skipped the one page the business itself publishes. A 14-site test returned
    # 8 real addresses that this function had been reporting as "not found".
    if _known_site:
        try:
            from app.services.website_harvester import harvest_one
            for hit in harvest_one(_known_site):
                if hit["email"] not in all_emails:
                    all_emails.insert(0, hit["email"])   # own site outranks a directory
                    sources_checked.append(f"Official website ({hit['source_url']})")
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    email = ""
    if all_emails:
        bad = {'justdial', 'sulekha', 'indiamart', 'tradeindia', 'google', 'sentry', 'schema', 'example', 'w3.org'}
        clean = [
            e for e in all_emails
            if not any(b in e.lower() for b in bad)
            and not _FOREIGN_TLD.search(e.split("@")[-1].lower())
        ]
        email = clean[0] if clean else ""
        all_emails = clean   # judge must only see India-plausible candidates

    # ── LLM judge (free — Cerebras, Ollama fallback): reviews the scraped
    # candidates and their sources, and picks only what it can attribute to
    # THIS company. It selects among found candidates — it can never invent one.
    judged = _llm_judge(company, city, phone_sources, list(dict.fromkeys(all_emails)))
    if judged is not None:
        j_phone, j_email = judged
        if j_phone and j_phone in phone_sources:
            confirmed = j_phone
            whatsapp = j_phone
            confidence = "HIGH"
            if "CerebrasJudge" not in sources_checked:
                sources_checked.append("CerebrasJudge")
        elif not j_phone and confidence == "MEDIUM":
            # Single-source candidate the judge could not attribute → don't verify
            confidence = "LOW"
        if j_email and j_email in {e.lower() for e in all_emails}:
            email = j_email

    # Final gate: reject the fabricated firstname.lastname@<company-slug> pattern
    # (e.g. shreyajeet.tiwari@stellarconsulting.com for "Stellar Consulting").
    # These are invented seed contacts. The auto-warm worker re-wrote exactly
    # this onto lead 92 minutes after it was quarantined, and verify() marked it
    # VALID. NOTE: this is deliberately narrower than _is_fake_email — we do NOT
    # strip role emails on the company domain (sales@synergytrading.in is a
    # legitimate real company address), only the personal-name pattern.
    if email and _is_personal_pattern_email(email, company):
        email = ""

    # Attribution gate: the mailbox must plausibly belong to THIS business.
    # Searching a company name returns real mailboxes owned by unrelated
    # entities — live examples that reached real strangers before this gate:
    #   "Zenith Distributors, Chennai" -> janed@zenith.co.zw      (Zimbabwe)
    #   "Stellar Holdings, Mumbai"     -> rahul_desai10@hotmail.com (a person)
    #   "Vanguard Services, Pune"      -> support@venkster.com    (unrelated co)
    # Each was a deliverable address, so MX/NXDOMAIN checks all passed — the
    # address was real, just not theirs. Unknown beats wrong: emailing a
    # stranger is spam and burns our sending reputation.
    if email:
        attributable, why = _email_attributable(email, company, city)
        if not attributable:
            email = ""
            if confidence != "NONE":
                confidence = "LOW"

    return {
        "confirmed_phone": confirmed,
        "whatsapp_number": whatsapp,
        "email": email,
        "sources_checked": sources_checked,
        "confidence": confidence,
        "all_phones_found": list(set(normalized)),
    }


# ── Website Finder + Deep Scraper ─────────────────────────────────────────────

_WALINK_RE = re.compile(r'wa\.me/(\d{10,13})|api\.whatsapp\.com/send\?phone=(\d{10,13})', re.I)

def _find_official_website(company: str, city: str) -> str:
    """Google search to find the company's official website URL."""
    queries = [
        f'{company} {city} official website',
        f'{company} {city} contact us',
    ]
    url_re = re.compile(r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,6})(?:/[^\s"<>]*)?')
    bad_domains = {
        'google', 'justdial', 'indiamart', 'sulekha', 'tradeindia', 'facebook',
        'instagram', 'twitter', 'linkedin', 'youtube', 'wikipedia', 'amazon',
        'flipkart', 'zomato', 'swiggy', 'maps', 'gstatic', 'w3.org',
    }
    for q in queries:
        try:
            url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
            with httpx.Client(timeout=10, headers={**_HEADERS, "Accept": "text/html"}, follow_redirects=True) as c:
                html = c.get(url).text
            for match in url_re.finditer(html):
                full_url = match.group(0)
                domain = match.group(1).lower()
                if not any(b in domain for b in bad_domains):
                    return full_url.split('"')[0].split("'")[0]
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return ""


def _scrape_website_full(website: str) -> dict:
    """
    Deep-scrape a company website: homepage + /contact + /about + /contact-us.
    Extracts emails, phones, and WhatsApp links.
    """
    if not website:
        return {"emails": [], "phones": [], "whatsapp": "", "website": website}

    base = website.rstrip('/')
    pages_to_try = [
        base,
        base + '/contact',
        base + '/contact-us',
        base + '/about',
        base + '/about-us',
        base + '/reach-us',
        base + '/get-in-touch',
    ]

    emails: list[str] = []
    phones: list[str] = []
    whatsapp = ""

    for url in pages_to_try:
        try:
            with httpx.Client(timeout=12, headers=_HEADERS, follow_redirects=True) as c:
                resp = c.get(url)
            if resp.status_code != 200:
                continue
            html = resp.text

            # Emails
            new_emails = _clean_emails(_EMAIL_RE.findall(html))
            emails.extend(new_emails)

            # Phone numbers
            new_phones = _phones_near_company(html, company)
            phones.extend(new_phones)

            # WhatsApp
            wa_matches = _WALINK_RE.findall(html)
            for m in wa_matches:
                num = m[0] or m[1]
                if num:
                    whatsapp = _norm(num)
                    break

            # Also check mailto: links — more reliable than regex on raw html
            mailto = re.findall(r'mailto:([^\s"\'<>?&]+)', html, re.I)
            emails.extend(_clean_emails(mailto))

            if emails or whatsapp:
                break  # Found what we need — stop paginating
        except Exception:
            continue

    return {
        "emails":   list(dict.fromkeys(emails))[:6],
        "phones":   list(dict.fromkeys(_norm(p) for p in phones))[:4],
        "whatsapp": whatsapp,
        "website":  website,
    }


# ── Email Finder ──────────────────────────────────────────────────────────────

def _scrape_website_email(website: str) -> list[str]:
    """Scrape a company's own website for contact emails (legacy wrapper)."""
    return _scrape_website_full(website)["emails"]


def _google_email_search(company: str, city: str, website: str = "") -> list[str]:
    """Google search for company email."""
    queries = [
        f'"{company}" {city} email contact',
        f'"{company}" "@" site:justdial.com OR site:indiamart.com OR site:sulekha.com',
    ]
    if website:
        domain = re.sub(r'https?://(www\.)?', '', website).split('/')[0]
        queries.insert(0, f'site:{domain} email contact')

    emails = []
    for q in queries:
        try:
            url = f"https://www.google.com/search?q={q.replace(' ', '+')}"
            with httpx.Client(timeout=10, headers={**_HEADERS, "Accept": "text/html"}, follow_redirects=True) as c:
                html = c.get(url).text
            found = _EMAIL_RE.findall(html)
            emails.extend(_clean_emails(found))
            if emails:
                break
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
    return emails[:5]


def _justdial_email(company: str, city: str) -> list[str]:
    """Try to extract email from JustDial company profile."""
    slug = company.lower().replace(' ', '-').replace('&', 'and')
    url = f"https://www.justdial.com/{city}/{slug}/nct-10215973"
    try:
        with httpx.Client(timeout=10, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get(url).text
        return _clean_emails(_EMAIL_RE.findall(html))
    except Exception:
        return []


def _indiamart_email(company: str, city: str) -> list[str]:
    """Try to extract email from IndiaMart company profile."""
    try:
        with httpx.Client(timeout=10, headers=_HEADERS, follow_redirects=True) as c:
            html = c.get("https://dir.indiamart.com/search.mp", params={"ss": company, "CatLGid": city}).text
        return _clean_emails(_EMAIL_RE.findall(html))
    except Exception:
        return []


def _guess_email_patterns(company: str, website: str = "") -> list[str]:
    """
    Generate likely email addresses ONLY against a real, verified website
    domain. Never invents a "{slug}.com" domain — that fabricates addresses
    on non-existent domains which bounce NXDOMAIN and violate the real-data
    policy. No verified website ⇒ no guess (email stays unknown).
    """
    if not website:
        return []
    domain = re.sub(r'https?://(www\.)?', '', website).split('/')[0].strip('/').lower()
    if not domain or "." not in domain:
        return []
    prefixes = ['info', 'contact', 'sales', 'business', 'enquiry', 'orders', 'procurement']
    return [f"{p}@{domain}" for p in prefixes]


# Consumer mailbox providers. A B2B buyer may genuinely use one, but we cannot
# attribute such an address to a specific company from a name search alone —
# "Stellar Holdings" matched rahul_desai10@hotmail.com, a private individual.
_FREEMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.in", "yahoo.co.in", "rediffmail.com", "ymail.com",
    "protonmail.com", "icloud.com", "aol.com", "zoho.com", "mail.com",
}

# Country TLDs that contradict an India-based lead. .com/.net/.org/.biz are
# global and stay allowed; only unambiguous foreign country codes are rejected.
_FOREIGN_CCTLD = {
    "zw", "au", "uk", "us", "ca", "nz", "za", "sg", "my", "ph", "id", "th",
    "vn", "cn", "jp", "kr", "ae", "sa", "om", "qa", "kw", "bh", "pk", "bd",
    "lk", "np", "de", "fr", "it", "es", "nl", "be", "se", "no", "dk", "fi",
    "pl", "ru", "br", "mx", "ar", "cl", "ke", "ng", "gh", "tz", "ug",
}

# Domain suffixes reserved for institutions that are never our buyer persona.
_INSTITUTIONAL = ("edu", "aero", "gov", "mil", "int")

_STOPWORDS = {
    "the", "and", "pvt", "ltd", "limited", "private", "llp", "inc", "co",
    "company", "corp", "corporation", "enterprises", "enterprise", "group",
    "industries", "industry", "solutions", "solution", "services", "service",
    "trading", "traders", "distributors", "distributor", "foods", "food",
    "consulting", "consultants", "holdings", "ventures", "capital", "partners",
    "retails", "retail", "mart", "store", "stores", "supermarket", "agencies",
    "agency", "international", "global", "india", "indian",
}


def _email_attributable(email: str, company: str, city: str = "") -> tuple[bool, str]:
    """
    Can this mailbox be attributed to THIS business? Returns (ok, reason).

    Deliberately conservative: a rejected address leaves the lead's email
    unknown (recoverable — the founder can add it), whereas accepting a wrong
    one emails a stranger (unrecoverable — that is spam, and it costs us
    sending reputation). Domain deliverability is checked separately; this is
    about identity, not reachability.
    """
    if not email or "@" not in email:
        return False, "no email"
    domain = email.split("@", 1)[1].lower().strip()
    if domain in _FREEMAIL:
        return False, f"consumer mailbox ({domain}) — cannot tie to a company"

    labels = domain.split(".")
    tld = labels[-1] if labels else ""
    if tld in _INSTITUTIONAL or (len(labels) > 2 and labels[-2] in ("edu", "gov", "ac")):
        return False, f"institutional domain (.{tld}) — not a B2B buyer"
    if tld in _FOREIGN_CCTLD:
        return False, f"foreign country domain (.{tld}) for an India-based lead"

    # The domain should share a distinctive word with the company name.
    # Generic words alone ("solutions", "trading") are not evidence — they are
    # exactly what produced the false matches.
    dom_base = re.sub(r"[^a-z0-9]", "", labels[0])
    tokens = [t for t in re.split(r"[^a-z0-9]+", (company or "").lower())
              if t and t not in _STOPWORDS and len(t) > 2]
    if not tokens:
        return False, "company name has no distinctive token to match on"
    if any(t in dom_base for t in tokens):
        return True, "domain matches the company name"
    return False, f"domain '{domain}' unrelated to company '{company}'"


def _is_personal_pattern_email(email: str, company: str) -> bool:
    """
    Detect the fabricated firstname.lastname@<company-slug> pattern specifically
    (e.g. shreyajeet.tiwari@stellarconsulting.com for "Stellar Consulting").

    Narrower than _is_fake_email on purpose: it requires BOTH the domain to be
    the company slug AND the local part to be a two-token personal name. A role
    address on the same domain (sales@, info@, contact@) is NOT flagged, because
    that is what a real company mailbox looks like — stripping it would delete
    genuine data. Domain existence is validated separately by MX verification.
    """
    if not email or "@" not in email:
        return False
    local, _, domain = email.lower().partition("@")
    slug = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    domain_base = domain.split(".")[0].replace("-", "")
    return bool(slug) and domain_base == slug and bool(re.match(r"^[a-z]+\.[a-z]+$", local))


def _is_fake_email(email: str, company: str) -> bool:
    """
    Detect auto-generated / fake emails:
    - domain is a slug of the company name (e.g. novadistributors.com for Nova Distributors)
    - sequential/pattern firstnames: sunil.saxena@, karan.singh@, etc. with company-slug domain
    """
    if not email or "@" not in email:
        return False
    domain = email.split("@")[1].lower()
    # Build slug from company name
    slug = re.sub(r"[^a-z0-9]", "", company.lower())[:25]
    # If domain starts with the company slug it's almost certainly generated
    domain_base = domain.split(".")[0]
    if slug and (domain_base.startswith(slug[:10]) or slug.startswith(domain_base[:10])):
        return True
    return False


def find_lead_email(company: str, city: str, website: str = "", existing_email: str = "",
                    force_replace: bool = False) -> dict:
    """
    Full contact discovery pipeline:
    1. If no website stored — Google-find the official website first
    2. Deep-scrape the website (homepage + /contact + /about) for email, phone, WhatsApp
    3. In parallel: search Google, JustDial, IndiaMart, Sulekha
    4. Fall back to domain pattern guesses if nothing found
    Returns email, phone, whatsapp, all_candidates, source, confidence, discovered_website.
    Set force_replace=True to re-search even when an email already exists.
    """
    # Step 0: return immediately if we already have a confirmed real email (unless force_replace)
    if existing_email and _clean_emails([existing_email]) and not force_replace:
        if not _is_fake_email(existing_email, company):
            return {
                "email": existing_email,
                "phone": "", "whatsapp": "",
                "all_candidates": [existing_email],
                "source": "existing",
                "confidence": "EXISTING",
                "discovered_website": website,
            }
    # Treat fake/forced-replace as if there's no existing email
    existing_email = ""

    # Step 1: find official website if not already known
    discovered_website = website
    if not website:
        try:
            discovered_website = _find_official_website(company, city)
        except Exception:
            discovered_website = ""

    # Step 2: deep-scrape the website in background + run directory scrapers in parallel
    all_email_candidates: list[str] = []
    source_map: dict[str, list[str]] = {}
    found_phone    = ""
    found_whatsapp = ""

    def _run_website_scrape():
        if discovered_website:
            return _scrape_website_full(discovered_website)
        return {"emails": [], "phones": [], "whatsapp": "", "website": ""}

    def _run_google():
        return _google_email_search(company, city, discovered_website)

    def _run_jd():
        return _justdial_email(company, city)

    def _run_im():
        return _indiamart_email(company, city)

    tasks = {
        "Website":   _run_website_scrape,
        "Google":    _run_google,
        "JustDial":  _run_jd,
        "IndiaMart": _run_im,
    }

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for fut in concurrent.futures.as_completed(futures, timeout=22):
            name = futures[fut]
            try:
                result = fut.result()
                if name == "Website":
                    # Website scraper returns a dict with emails/phones/whatsapp
                    emails = result.get("emails", [])
                    phones = result.get("phones", [])
                    wa     = result.get("whatsapp", "")
                    source_map[name] = emails
                    all_email_candidates.extend(emails)
                    if phones and not found_phone:
                        found_phone = phones[0]
                    if wa and not found_whatsapp:
                        found_whatsapp = wa
                else:
                    # Other scrapers return list[str] of emails
                    source_map[name] = result
                    all_email_candidates.extend(result)
            except Exception as _exc:
                # Swallowed on purpose — this path must not break the
                # caller — but never silently: a failure with no name is
                # how the category engine fell back for hours unnoticed.
                _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Deduplicate preserving first-seen order
    seen, deduped = set(), []
    for e in all_email_candidates:
        if e not in seen:
            seen.add(e); deduped.append(e)

    # Determine best email + confidence
    if deduped:
        best = deduped[0]
        hits = sum(1 for emails in source_map.values() if best in emails)
        confidence = "HIGH" if hits >= 2 else "MEDIUM"
        source = next((s for s, emails in source_map.items() if best in emails), "unknown")
    else:
        guesses = _guess_email_patterns(company, discovered_website)
        best = guesses[0] if guesses else ""
        confidence = "GUESSED" if best else "NONE"
        source = "pattern_guess"
        deduped = guesses

    return {
        "email":              best,
        "phone":              found_phone,
        "whatsapp":           found_whatsapp,
        "all_candidates":     deduped[:8],
        "source":             source,
        "confidence":         confidence,
        "discovered_website": discovered_website,
    }
