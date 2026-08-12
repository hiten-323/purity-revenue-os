"""
Gmail SMTP sender for Purity Beans outreach emails.
connect@purepantryprovisions.com is linked to Gmail.
Requires a Gmail App Password (16 chars) from myaccount.google.com → Security → App Passwords.
"""
from __future__ import annotations
import smtplib, os, re

# Load .env HERE rather than relying on the entry point to have done it.
# Reading the credential at import time with a bare os.getenv meant the value
# existed only in processes that happened to call load_dotenv first: the API
# had it, the worker did not. So the worker's reply poller reported
# "ZOHO_APP_PASSWORD not configured" on every cycle and never opened the inbox
# at all — which means our "0 replies" was partly a measurement failure, not
# a market verdict. A module that needs a secret should load it itself.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", ".env"))
except Exception as _e:
    print(f"[email_sender] .env load skipped: {_e}")
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session
from app.models.models import B2BLead, AgentLog
import logging

_log = logging.getLogger(__name__)

SMTP_HOST = "smtp.zoho.in"   # Zoho India server (zoho.com for non-India)
SMTP_PORT = 587              # STARTTLS

SENDER_EMAIL    = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
SENDER_PASSWORD = os.getenv("ZOHO_APP_PASSWORD", "")   # Zoho App Password (NOT your login password)
SENDER_NAME     = os.getenv("SENDER_NAME", "Hiten Jain | Pure Pantry Provisions")


@dataclass
class OutreachEmail:
    to_email: str
    to_name: str
    company: str
    subject: str
    body_text: str
    body_html: str = ""
    sent_at: str = ""
    status: str = "pending"
    error: str = ""
    message_id: str = ""
    smtp_response: str = ""
    # Carried through so send_email can look the lead up and enforce the
    # verified-address gate. Previously lead_id reached build_outreach_email
    # but was used only for the tracking pixel and never stored here, so any
    # guard reading email.lead_id silently saw None and never fired.
    lead_id: int | None = None


def domain_is_deliverable(email_addr: str, timeout: float = 5.0) -> bool:
    """
    Live DNS gate: True only if the recipient domain actually exists and can
    receive mail. Last line of defence against fabricated/guessed domains that
    bounce NXDOMAIN (5.4.4) and wreck sender reputation.

    NOTE: this host's system nameserver is unreliable (lookups time out), so we
    resolve via the OS resolver first, then via public DNS (8.8.8.8 / 1.1.1.1)
    for a definitive MX/NXDOMAIN answer. Never depend on the default resolver.
    """
    try:
        domain = email_addr.split("@", 1)[1].strip().lower()
    except (IndexError, AttributeError):
        return False
    if not domain or "." not in domain:
        return False

    # 1) OS resolver A-record check — fast and reliable on this box.
    import socket
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(domain)
        return True   # domain resolves → real
    except socket.gaierror:
        pass          # doesn't resolve via OS — confirm via public DNS below
    except Exception as _exc:
        # Swallowed on purpose — this path must not break the
        # caller — but never silently: a failure with no name is
        # how the category engine fell back for hours unnoticed.
        _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # 2) Public-DNS MX/A check for a definitive answer (handles domains with MX
    #    but no A record, and confirms NXDOMAIN).
    try:
        import dns.resolver
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = ["8.8.8.8", "1.1.1.1"]
        r.lifetime = timeout
        r.timeout = timeout
        for rtype in ("MX", "A", "AAAA"):
            try:
                if r.resolve(domain, rtype):
                    return True
            except dns.resolver.NXDOMAIN:
                return False          # domain does not exist at all
            except dns.resolver.NoAnswer:
                continue              # no record of this type — try the next
        return False
    except dns.resolver.NXDOMAIN:
        return False
    except Exception:
        # Both resolvers failed for network reasons — inconclusive. Let SMTP be
        # the final arbiter rather than dropping a possibly-good send.
        return True


def send_email(email: OutreachEmail) -> OutreachEmail:
    if not SENDER_PASSWORD:
        email.status = "failed"
        email.error = "ZOHO_APP_PASSWORD not set in .env — get it from accounts.zoho.in → Security → App Passwords"
        return email

    # Hard gate: never send to a domain that does not resolve (NXDOMAIN).
    if not domain_is_deliverable(email.to_email):
        email.status = "failed"
        email.error = f"BLOCKED: domain does not exist (NXDOMAIN) for {email.to_email} — not sent"
        return email

    # ── Governance gates, enforced at the single chokepoint ──
    #
    # Five endpoints call send_email (approve-journey/bg_send_emails,
    # send_approved_emails, one_click_execute, tender_approve_and_send,
    # push_distributor_draft). Gating them individually left two ungated, so
    # both checks live HERE — every path goes through this function, so there
    # is no way around them.
    #
    # Gate A: a prospect must have a VERIFIED address. An unverified address is
    # a guess, and guesses are what produced 63 NXDOMAIN failures and got the
    # Zoho account flagged. Mail addressed to the founder's own inbox (draft
    # previews) is exempt — that is not outbound to a prospect.
    _to = (email.to_email or "").strip().lower()
    _is_self = _to == (SENDER_EMAIL or "").strip().lower()
    if not _is_self and getattr(email, "lead_id", None):
        try:
            from app.database.database import SessionLocal
            from app.models.models import B2BLead
            _db = SessionLocal()
            try:
                _lead = _db.query(B2BLead).filter(B2BLead.id == email.lead_id).first()
                if _lead is not None:
                    # ONE authority on whether an address may be used. This
                    # block used to decide for itself and disagreed with the
                    # trust engine: the engine cleared 32 contacts and this
                    # refused 29 of them, so the queue advertised sends the
                    # sender would never make. That is the same defect that
                    # once put "3 email ready" on screen with zero sendable.
                    from app.services.trust_promoter import may_send as _may
                    _ok, _why = _may(_lead)
                    if not _ok:
                        email.status = "failed"
                        email.error = f"BLOCKED: {email.to_email} — {_why}"
                        return email
                    is_verified = True
                    # email_verification_status == "VALID" is deliberately NOT
                    # accepted on its own. 82 addresses were found carrying it
                    # having never passed the verifier — written into SQLite with
                    # no event, because this application is not the only writer.
                    # A column another process can set is not a trust signal.
                    if not is_verified:
                        email.status = "failed"
                        email.error = (f"BLOCKED: {email.to_email} is on file but not verified "
                                       f"(email_verified=false) — verify the address before sending")
                        return email

                    # The flag alone is not enough either, for the same reason.
                    # Re-verify HERE, at the last moment before the message
                    # leaves — the only check an out-of-band write cannot get
                    # ahead of. Trust the founder's own evidence: an address the
                    # buyer supplied on a call, or one that has already replied,
                    # is better proof than any DNS lookup.
                    _trust = (getattr(_lead, "email_verification_status", "") or "").upper()
                    if _trust not in ("FOUNDER_CALL_PROVIDED", "REPLIED"):
                        try:
                            from app.services.email_verifier import verify_email
                            # 4th arg is `website`; this passed it as `division`.
                            _v = verify_email(email.to_email, _lead.company or "",
                                              getattr(_lead, "division", "") or "",
                                              _lead.website or "")
                            _st = (_v.get("status") or "UNVERIFIED").upper()
                            # Block only on CONCLUSIVE failure. Requiring
                            # status == "VALID" rejected every catch-all
                            # domain, which is most of the Indian SMB book —
                            # 26 of 32 sendable contacts, all with valid MX.
                            if (_st == "INVALID" or not _v.get("mx_valid")
                                    or _v.get("is_disposable")):
                                email.status = "failed"
                                email.error = (
                                    f"BLOCKED: {email.to_email} conclusively "
                                    f"undeliverable at send time "
                                    f"({_v.get('reason')})")
                                return email
                        except Exception:
                            pass      # inconclusive never blocks a good send
            finally:
                _db.close()
        except Exception:
            pass          # never let the guard itself break a legitimate send

    # Gate A2: account-level governance. Suppression and the company frequency
    # cap are decided per ACCOUNT, not per contact, so no individual sender can
    # evaluate them correctly on its own.
    #
    # Seven call sites reach SMTP and five write EMAIL_SENT. Fixing them one by
    # one is what left two paths ungated the last time, so this lives at the
    # chokepoint every caller must pass. Cadence is deliberately NOT enforced
    # here — a reply response is not a scheduled touch, and blocking it would
    # silence us exactly when a buyer is talking. Cadence stays in the queue,
    # which knows the difference.
    if not _is_self and getattr(email, "lead_id", None):
        from app.database.database import SessionLocal
        from app.models.models import B2BLead, WorkflowEvent
        from app.services.account_graph import account_for
        _db = SessionLocal()
        try:
            _lead = _db.query(B2BLead).filter(B2BLead.id == email.lead_id).first()
            if _lead is not None:
                _acct = account_for(_lead, _db)
                _ids = [x.id for x in _acct["leads"]]
                _stop = _db.query(WorkflowEvent).filter(
                    WorkflowEvent.lead_id.in_(_ids),
                    WorkflowEvent.event_type.in_(
                        ["UNSUBSCRIBED", "DO_NOT_CONTACT", "COMPLAINT"])).first()
                if _stop is not None:
                    email.status = "failed"
                    email.error = (f"BLOCKED: {_acct['name']} asked not to be "
                                   f"contacted ({_stop.event_type}) — suppression "
                                   f"is account-wide and absolute")
                    return email
        finally:
            _db.close()

    # Gate B: provider-level throttling / account blocks and our own volume,
    # pace and failure limits. Previously only bg_send_emails consulted this,
    # so the other four paths could hammer a flagged account.
    if not _is_self:
        try:
            from app.database.database import SessionLocal
            from app.services.deliverability import check_send_allowed
            _db = SessionLocal()
            try:
                _v = check_send_allowed(_db)
                if not _v.allowed:
                    email.status = "failed"
                    email.error = f"HELD: {_v.reason}"
                    return email
            finally:
                _db.close()
        except Exception as _exc:
            # Swallowed on purpose — this path must not break the
            # caller — but never silently: a failure with no name is
            # how the category engine fell back for hours unnoticed.
            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)

    # Preserve a caller-supplied Message-ID (so the DB record and the actually
    # sent header match for IMAP reconciliation); only generate one if absent.
    from email.utils import make_msgid
    msg_id = email.message_id or make_msgid(domain="purepantryprovisions.com")
    email.message_id = msg_id

    msg = MIMEMultipart("alternative")
    msg["Subject"] = email.subject
    msg["From"]    = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"]      = f"{email.to_name} <{email.to_email}>" if email.to_name else email.to_email
    msg["Reply-To"] = SENDER_EMAIL
    msg["Message-ID"] = msg_id

    msg.attach(MIMEText(email.body_text, "plain", "utf-8"))
    msg.attach(MIMEText(email.body_html or _text_to_html(email.body_text), "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(SENDER_EMAIL, SENDER_PASSWORD)
            smtp.sendmail(SENDER_EMAIL, email.to_email, msg.as_string())
        email.status = "sent"
        email.sent_at = datetime.utcnow().isoformat()
        email.smtp_response = "250 OK - Accepted for delivery"
        print(f"SMTP sent successfully. MsgID: {msg_id}")
    except smtplib.SMTPAuthenticationError:
        email.status = "failed"
        email.error = "Zoho auth failed — use App Password from accounts.zoho.in, not your login password"
    except smtplib.SMTPException as e:
        email.status = "failed"
        email.error = str(e)
    except Exception as e:
        email.status = "failed"
        email.error = str(e)

    return email


def send_batch(emails: list[OutreachEmail]) -> list[OutreachEmail]:
    return [send_email(e) for e in emails]


def build_outreach_email(
    to_email: str,
    to_name: str,
    company: str,
    subject: str,
    body: str,
    lead_id: int | None = None,
) -> OutreachEmail:
    signature = (
        f"\n\n--\n{SENDER_NAME}\n"
        f"Pure Pantry Provisions\n"
        f"{SENDER_EMAIL}"
    )
    body_text = body + signature

    # Embed tracking pixel in HTML version
    # Must be a host that is actually reachable from the RECIPIENT's mail
    # client, not just from this machine. The previous default,
    # api.p3online.in, has no DNS record at all — so the pixel in every email
    # ever sent pointed at a dead host and no open could ever be recorded.
    # dashboard.p3online.in is the live cloudflared tunnel and serves this path
    # (verified: 200, content-type image/gif).
    api_base = os.getenv("API_BASE_URL", "https://dashboard.p3online.in")
    pixel_html = ""
    if lead_id:
        pixel_html = f'<img src="{api_base}/api/v1/b2b/track/open/{lead_id}" width="1" height="1" style="display:none" alt="" />'

    html_body = _text_to_html(body_text) + pixel_html

    return OutreachEmail(
        to_email=to_email,
        to_name=to_name,
        company=company,
        subject=subject,
        body_text=body_text,
        body_html=html_body,
        lead_id=lead_id,
    )


def _text_to_html(text: str) -> str:
    lines = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = "<br>".join(lines.splitlines())
    return (
        '<html><body style="font-family:Arial,sans-serif;font-size:14px;'
        'color:#333;line-height:1.7;max-width:600px">'
        f"{html_body}"
        "</body></html>"
    )

from app.services.crm_tracker import CRMTrackerService

# Prices live in ONE place: outreach_engine.SKUS, from the Product Catalogue
# 2026. MRP is uniform for a variant across every channel, so there is no second
# price list to keep in step.
#
# The table that used to sit here quoted Ultra Blend 50 g at ₹302.40 as an
# "institutional" rate against a catalogue MRP of ₹209 — above retail, and
# contradicting Purica, which ran the other way. It was dead code, referenced
# nowhere, which is exactly why it survived: a stale price list nothing renders
# is invisible until someone wires it back in and quotes it to a customer.

# ── Segment-specific one-liners (no unverified claims) ───────────────────────
_SEGMENT_CONTEXT = {
    "corporate":    "We understand {company} manages pantry services for a significant number of employees across its offices.",
    "horeca":       "We understand {company} maintains high hospitality standards where morning coffee directly shapes the guest experience.",
    "distributor":  "We understand {company} serves regional wholesale and retail networks where fast-moving, high-margin SKUs drive business.",
    "wholesale":    "We understand {company} serves regional wholesale and retail networks where fast-moving, high-margin SKUs drive business.",
    "retail":       "We understand {company} caters to quality-conscious consumers where clean-label products command premium shelf space.",
    "government":   "We understand {company} serves a wide network of employees and consumers through its institutional and retail operations.",
    "gifting":      "We understand {company} curates premium gifting solutions where product quality and presentation are paramount.",
}

_SEGMENT_FOCUS = {
    "corporate":   (
        "For office pantry supply we offer:\n"
        "• Direct wholesale contract pricing\n"
        "• Bulk packs for high-volume consumption\n"
        "• PAN India dispatch with next-day shipping on orders above minimum quantity"
    ),
    "horeca":      (
        "For hospitality supply we offer:\n"
        "• Single-serve sachets and bulk jars\n"
        "• Dispenser-compatible formats\n"
        "• Bulk pricing for breakfast service and in-room amenities"
    ),
    "distributor": (
        "For distribution partnerships we offer:\n"
        "• Trade margins of 35–42% on MRP\n"
        "• Exclusive regional territory (selective)\n"
        "• Fast-moving freeze-dried SKUs with strong shelf rotation"
    ),
    "wholesale":   (
        "For wholesale partnerships we offer:\n"
        "• Trade margins of 35–42% on MRP\n"
        "• Bulk pack formats for retailer redistribution\n"
        "• Consistent supply with PAN India logistics"
    ),
    "retail":      (
        "For retail supply we offer:\n"
        "• Clean-label premium positioning\n"
        "• Display-ready packaging\n"
        "• Competitive retail margins and replenishment support"
    ),
    "government":  (
        "For institutional supply we offer:\n"
        "• GeM-listed and direct procurement options\n"
        "• Bulk institutional pricing with volume discounts\n"
        "• FSSAI-licensed, GST-compliant supply chain"
    ),
    "gifting":     (
        "For gifting supply we offer:\n"
        "• Premium gift-ready packaging\n"
        "• Custom assortment boxes\n"
        "• Bulk corporate gifting pricing"
    ),
}

def _greeting_for(lead: B2BLead) -> str:
    """
    Safe greeting rules:
    - Government / GeM leads: always 'Dear Procurement Team,' or 'Dear Purchase Officer,'
      (never assume a name or title from scraped data; institutional contacts change often)
    - Corporate / HORECA: use first name if known, else 'Dear Sir/Madam,'
    - Distributor / Retail / Wholesale / Gifting: use first name if known, else 'Dear Sir/Madam,'
    """
    div = (lead.division or "corporate").lower()
    if div == "government":
        return "Dear Procurement Team,"
    name = (lead.contact_name or "").strip()
    first_name = name.split()[0] if name else ""
    return f"Dear {first_name}," if first_name else "Dear Sir/Madam,"


def _closing_for(lead: B2BLead) -> str:
    """
    GeM is a platform, not the buyer — closing line must reflect that.
    All other companies: 'work with your organisation.'
    """
    company_lower = lead.company.lower()
    if "gem" in company_lower or "government e-marketplace" in company_lower:
        return (
            "We look forward to supporting government departments and "
            "public sector organisations through the GeM platform."
        )
    return "We look forward to the opportunity to work with your organisation."


def get_personalized_intro(lead: B2BLead) -> str:
    """Return a safe, verifiable opening — no unverified expansion claims."""
    greeting = _greeting_for(lead)
    div = (lead.division or "").lower()
    # Falling back to the corporate line is what told "Balaji's Mart", a kirana,
    # that it "manages pantry services for employees across its offices" — the
    # classifier emits grocery / hotel_canteen / wholesaler_agglo and this map
    # has none of them, so nearly every draft became the pantry pitch. The
    # category pitches now live in outreach_engine, keyed on what the classifier
    # actually produces; an unmapped category raises there rather than guessing.
    from app.services.outreach_engine import pitch_for, UnmappedCategory
    try:
        context = pitch_for(div).context.format(company=lead.company)
    except UnmappedCategory:
        # Say nothing about their business rather than something wrong about it.
        context = "I am writing from Pure Pantry Provisions about Purity Beans instant coffee."
    return f"{greeting}\n\n{context}"


# ── Category-aware email profiles ─────────────────────────────────────────────
# Each category gets its own subject hook, "why we selected you" framing, value
# angle (the benefit THAT category cares about), use case, and next step. Every
# line uses only verified product facts + the lead's real fields — no invented
# claims about the recipient. Expected annual opportunity stays INTERNAL and is
# never written into the email.

_CATEGORY_PROFILE: dict[str, dict] = {
    "distributor": {
        "subject": "Distribution Partnership — Purity Beans Premium Coffee",
        "why": "we're expanding our distribution network{loc} and are looking for established partners who move FMCG and beverage lines.",
        "angle": "Why distributors partner with Purity Beans",
        "bullets": ["Healthy trade margins on fast-moving freeze-dried SKUs",
                    "Selective regional territory and dealer support",
                    "Consistent PAN-India supply and strong shelf rotation"],
        "use_case": "supplying retailers, kirana networks, and institutional buyers in your territory",
        "next_step": "we'd be glad to share the dealer margin sheet and send a complimentary sample kit",
    },
    "corporate": {
        "subject": "Office Pantry Coffee Supply — {company}",
        "why": "we work with offices{loc} that want dependable, premium pantry coffee for their teams.",
        "angle": "Why corporate offices choose Purity Beans",
        "bullets": ["Consistent premium quality for staff and guest pantries",
                    "Reliable replenishment with transparent contract pricing",
                    "Simple procurement — single point of contact, PAN-India dispatch"],
        "use_case": "office pantry supply, employee beverage programs, and meeting-room refreshments",
        "next_step": "we can send complimentary samples for your pantry team to try",
    },
    "hotel": {
        "subject": "Premium Coffee Supply for {company}",
        "why": "we supply hospitality businesses{loc} where the morning coffee directly shapes the guest experience.",
        "angle": "Why hotels choose Purity Beans",
        "bullets": ["Premium, consistent taste guests remember",
                    "Single-serve sachets and bulk jars for breakfast and in-room service",
                    "Dependable supply so you never run short in season"],
        "use_case": "in-room amenities, breakfast service, and banquet/conference catering",
        "next_step": "we'd be happy to arrange a complimentary tasting for your F&B team",
    },
    "cafe": {
        "subject": "Coffee Supply Partnership — {company}",
        "why": "we work with cafés{loc} that care about beverage quality and repeat customers.",
        "angle": "Why cafés choose Purity Beans",
        "bullets": ["100% coffee, no chicory — a clean, premium cup",
                    "Freeze-dried Arabica & Robusta options for your menu",
                    "Consistent supply that keeps your regulars coming back"],
        "use_case": "your instant/base coffee menu and high-volume service",
        "next_step": "we can send samples so your team can taste the difference",
    },
    "retail": {
        "subject": "Stock Purity Beans Coffee — {company}",
        "why": "we're placing our range with quality-focused retailers{loc}.",
        "angle": "Why retailers stock Purity Beans",
        "bullets": ["Clean-label premium positioning with strong sell-through",
                    "Display-ready packaging with shelf appeal",
                    "Competitive retail margins and replenishment support"],
        "use_case": "shelf placement, promotions, and repeat consumer purchase",
        "next_step": "we can share the retail margin sheet and a sample pack",
    },
    "facility_management": {
        "subject": "Multi-Site Coffee Supply — {company}",
        "why": "we support facility and pantry-management companies{loc} that run beverage supply across multiple client sites.",
        "angle": "Why facility management companies choose Purity Beans",
        "bullets": ["Centralised, multi-site supply with a single point of contact",
                    "Consolidated billing and transparent contract pricing",
                    "Reliable PAN-India dispatch across all your locations"],
        "use_case": "centralised pantry supply across the sites you manage",
        "next_step": "we can put together a multi-site supply proposal and send samples",
    },
    "hospital": {
        "subject": "Reliable Pantry Coffee Supply — {company}",
        "why": "we supply institutional pantries{loc} that need dependable, hygienic beverage supply.",
        "angle": "Why healthcare institutions choose Purity Beans",
        "bullets": ["FSSAI-licensed, hygienic, GST-compliant supply chain",
                    "Reliable replenishment for staff, admin, and cafeteria pantries",
                    "Bulk formats with transparent institutional pricing"],
        "use_case": "staff pantries, administrative areas, and cafeteria service",
        "next_step": "we'd be glad to share institutional pricing and send samples",
    },
    "government": {
        "subject": "Institutional Coffee Supply — {company}",
        "why": "we serve institutional and government buyers{loc} through compliant, direct and GeM procurement.",
        "angle": "Why institutions choose Purity Beans",
        "bullets": ["FSSAI-licensed, MSME-registered, GST-compliant",
                    "GeM-listed and direct procurement options",
                    "Bulk institutional pricing with documented compliance"],
        "use_case": "canteen, pantry, and institutional catering requirements",
        "next_step": "we can share our compliance documents and institutional price list",
    },
    "gifting": {
        "subject": "Premium Coffee Gifting — {company}",
        "why": "we work with gifting and event companies{loc} that curate premium assortments.",
        "angle": "Why gifting companies choose Purity Beans",
        "bullets": ["Premium, gift-ready presentation",
                    "Custom assortment boxes and festive packs",
                    "Bulk corporate-gifting pricing and dependable timelines"],
        "use_case": "festive hampers, corporate gifting, and event packs",
        "next_step": "we can share gifting formats and send a sample assortment",
    },
    "education": {
        "subject": "Campus Pantry Coffee Supply — {company}",
        "why": "we supply educational institutions{loc} across their staff rooms, cafeterias, and administration.",
        "angle": "Why institutions choose Purity Beans",
        "bullets": ["Consistent premium quality for staff and faculty pantries",
                    "Bulk formats for cafeterias and canteens",
                    "Reliable supply with transparent institutional pricing"],
        "use_case": "staff rooms, cafeterias, and administrative pantries",
        "next_step": "we'd be happy to share institutional pricing and send samples",
    },
}
_CATEGORY_PROFILE["wholesale"] = _CATEGORY_PROFILE["distributor"]
_CATEGORY_PROFILE["horeca"] = _CATEGORY_PROFILE["hotel"]

_CATEGORY_KEYWORDS = [
    ("hospital",            ("hospital", "clinic", "medical", "healthcare", "nursing")),
    ("education",           ("school", "college", "university", "institute", "academy", "vidyalaya", "campus", "coaching")),
    ("facility_management", ("facility", "facilities", "management services", "housekeeping", "pantry management")),
    ("cafe",                ("cafe", "café", "coffee house", "bakery", "bistro", "brew")),
    ("hotel",               ("hotel", "resort", "inn", "residency", "hospitality", "banquet")),
    ("gifting",             ("gift", "hamper")),
]


def _resolve_email_category(lead: B2BLead) -> str:
    """Map a lead to its finest email category using verified fields + name
    signals. Falls back to the broad division/segment, then 'corporate'."""
    seg = (lead.segment or lead.division or "").lower()
    if seg in _CATEGORY_PROFILE and seg not in ("corporate", "horeca"):
        return seg
    hay = f"{lead.company or ''} {lead.industry or ''} {lead.division or ''} {lead.segment or ''}".lower()
    # Word-boundary match so "hospitality" does not trip the "hospital" keyword.
    for cat, kws in _CATEGORY_KEYWORDS:
        if any(re.search(rf"\b{re.escape(k)}\b", hay) for k in kws):
            return cat
    if seg in _CATEGORY_PROFILE:
        return seg
    return "corporate"


def generate_b2b_pitch_email(lead: B2BLead) -> tuple[str, str]:
    """
    Every EmailDraft in the system is created through this one function, so it
    is where the category engine belongs. Routing it here fixed all four draft
    creators at once — and it is why "Quality Mart", a grocery, sat in the
    Approval Center with a pantry pitch: the draft had been generated on 01 Jul
    by the old path and stored, so fixing the generator alone changed nothing
    that was already written.

    Falls through to the legacy body only for a category the engine has no pitch
    for, which raises there rather than guessing.
    """
    try:
        from app.services.outreach_engine import build_draft, relationship
        from app.services.outreach_templates import SIGNATURE
        from app.database.database import SessionLocal
        from app.models.models import WorkflowEvent, LeadInteraction

        db = SessionLocal()
        try:
            ev = db.query(WorkflowEvent).filter(WorkflowEvent.lead_id == lead.id).all()
            inter = db.query(LeadInteraction).filter(
                LeadInteraction.lead_id == lead.id,
                LeadInteraction.superseded_by_id.is_(None)).all()
            rel = relationship(ev, inter)
            mem = {}
            for i in sorted(inter, key=lambda x: x.occurred_at or datetime.min):
                for fld in ("decision_maker", "current_supplier", "preferred_contact_time",
                            "monthly_consumption_kg"):
                    v = getattr(i, fld, None)
                    if v not in (None, ""):
                        mem[fld] = v
            for fld in ("decision_maker", "current_supplier"):
                if not mem.get(fld) and getattr(lead, fld, None):
                    mem[fld] = getattr(lead, fld)
            sent = [e for e in ev if e.event_type == "EMAIL_SENT"]
            replied = any("REPLI" in (e.event_type or "") for e in ev)
            days = (datetime.utcnow() - max(e.occurred_at for e in sent)).days if sent else None
            d = build_draft(lead, mem, len(sent), replied, days, SIGNATURE, rel=rel)
            return d["subject"], d["body"]
        finally:
            db.close()
    except Exception as _e:
        # Silence here cost real drafts: every email fell back to the generic
        # body and nobody could see why. Log it, keep falling through.
        import logging
        logging.getLogger(__name__).warning(
            "category engine unavailable for lead %s (%s) — using legacy body: %s: %s",
            getattr(lead, "id", "?"), getattr(lead, "division", "?"),
            type(_e).__name__, _e)

    """
    Category-aware B2B outreach email. Picks the finest matching category
    (distributor / corporate / hotel / cafe / retail / facility_management /
    hospital / government / gifting / education) and tailors the subject, the
    'why we selected you' line, the value angle, use case, and next step to it.

    Rules enforced:
      - Only verified product facts + the lead's real fields (company, city,
        decision-maker name if known) — never invents claims about the recipient
      - Expected annual opportunity is INTERNAL — never written into the email
      - No 'We manufacture' (brand/trader), no founder story, no MRP
      - Role-safe greeting; institutional buyers get a neutral salutation
    """
    cat = _resolve_email_category(lead)
    company = (lead.company or "").strip()
    greeting = _greeting_for(lead)
    city_part = f" in {lead.city}" if lead.city else ""

    is_verified = cat not in ("", "unknown", "needs_reclassification")
    
    if is_verified:
        if cat in ("distributor", "wholesale", "wholesaler"):
            why_line = f"I came across {company}{city_part} while researching food distribution businesses in your region."
            prop_line = "We are exploring distribution opportunities for Purity Beans and wanted to see whether the range could be relevant to your network."
            cta = "Would you be open to reviewing our catalogue and distributor pricing?"
        elif cat in ("retail_kirana", "retail", "grocery_chain"):
            why_line = f"I came across {company}{city_part} while researching retail stores in your region."
            prop_line = "We are expanding retail availability of Purity Beans and wanted to explore whether the range could be relevant for your store."
            cta = "May I share our catalogue and retailer pricing?"
        elif cat in ("supermarket", "modern_trade"):
            why_line = f"I came across {company}{city_part} while researching modern trade and supermarket outlets in your region."
            prop_line = "We are expanding the retail presence of Purity Beans and would like to explore a potential listing with your stores."
            cta = "Would it be useful if I shared our product catalogue and trade terms?"
        elif cat in ("corporate_office", "office_pantry", "manufacturing", "facility_management", "hospital", "school", "college", "government", "institutional_buyer"):
            why_line = f"I came across {company}{city_part} while researching organizations and workspace facility networks in your region."
            prop_line = "We supply instant coffee for workplace and pantry requirements and wanted to understand whether this may be relevant to your organisation."
            cta = "May I share our institutional range for consideration?"
        elif cat in ("hotel", "restaurant", "cafe", "horeca"):
            why_line = f"I came across {company}{city_part} while researching hospitality and F&B venues in your region."
            prop_line = "We wanted to explore whether Purity Beans could be relevant to your beverage requirements."
            cta = "Would you be open to reviewing our range and trade pricing?"
        else:
            why_line = f"I came across {company}{city_part} while researching businesses in your region."
            prop_line = "We are reaching out to explore potential opportunities to work with your organization."
            cta = "Would you be open to reviewing our catalogue and trade pricing?"
    else:
        why_line = f"I came across {company}{city_part} while researching businesses in your region that may have a potential requirement for our instant coffee range."
        prop_line = "We are reaching out to explore whether our instant coffee portfolio is relevant to your operations."
        cta = "Would you be open to reviewing our catalogue and trade pricing?"

    subject = f"Purity Beans instant coffee — {company}"

    body = f"""{greeting}

I hope you are doing well.

{why_line} {prop_line}

Purity Beans is a premium instant coffee brand of Pure Pantry Provisions. We offer a 100% pure instant coffee range — including both premium freeze-dried and agglomerated options — with zero chicory, zero artificial flavors, and no additives.

We're FSSAI Licensed, MSME Registered, GST Compliant, with reliable PAN-India supply.

{cta}

Warm regards,
Hiten Jain
Founder | Pure Pantry Provisions
Purity Beans — Premium Instant Coffee
📞 +91 90849 58495 | WhatsApp: +91 98555 93323
✉ connect@purepantryprovisions.com | 🌐 p3online.in"""

    return subject, body


# ── Email quality gate ────────────────────────────────────────────────────────
# Red flags that should block or warn before any email is sent.

_QUALITY_FLAGS = [
    {
        "id": "unverified_claim",
        "label": "Unverified claim",
        "patterns": [
            "i noticed", "i heard", "i saw that", "i read that",
            "is expanding", "is growing", "is opening", "is launching",
            "recently raised", "just announced",
        ],
        "severity": "block",
        "tip": "Remove unverified claims about the recipient's activities. Use factual segment context instead.",
    },
    {
        "id": "false_manufacturer_claim",
        "label": "False manufacturer claim",
        "patterns": [
            "we manufacture", "our factory", "our manufacturing", "manufactured by us",
            "we produce", "our production facility",
        ],
        "severity": "block",
        "tip": "Pure Pantry Provisions is a brand/trader, not a manufacturer. Use 'We offer' or 'We supply' instead.",
    },
    {
        "id": "gem_closing_error",
        "label": "GeM addressed as buyer",
        "patterns": [
            "look forward to working with gem",
            "look forward to work with gem",
            "opportunity to work with gem",
        ],
        "severity": "block",
        "tip": "GeM is a platform, not the buyer. Use: 'We look forward to supporting government departments through the GeM platform.'",
    },
    {
        "id": "founder_story",
        "label": "Founder personal story",
        "patterns": [
            "coma", "medical accident", "survived", "recovery", "personal note",
        ],
        "severity": "block",
        "tip": "Remove the founder story from first outreach. Move it to your website or company profile.",
    },
    {
        "id": "mrp_shown",
        "label": "MRP shown to buyer",
        "patterns": ["mrp ₹", "mrp rs", "mrp rs.", "m.r.p"],
        "severity": "block",
        "tip": "Institutional buyers should only see institutional price, not MRP.",
    },
    {
        "id": "too_long",
        "label": "Email too long",
        "patterns": [],   # checked by word count
        "word_limit": 260,
        "severity": "warn",
        "tip": "Keep first-touch emails under 220–260 words. Procurement managers skim; shorter wins.",
    },
    {
        "id": "generic_greeting",
        "label": "Generic greeting",
        "patterns": ["hi md,", "hi md ", "dear md,", "dear sir or madam", "to whom it may concern", "hi there,"],
        "severity": "warn",
        "tip": "Use the recipient's name or 'Dear Procurement Team,' — never 'Hi MD'.",
    },
    {
        "id": "numbered_reply_prompt",
        "label": "Numbered reply prompt",
        "patterns": ["reply with:\n1.", "reply with:\n\n1.", "1. sample required", "2. call back required"],
        "severity": "warn",
        "tip": "Replace numbered reply options with a single, natural CTA: 'Let us know a convenient time for a short call.'",
    },
]


def validate_email_quality(subject: str, body: str) -> dict:
    """
    Run the automated quality gate on a draft email.
    Returns:
      {
        "score": int (0–100),
        "grade": "A" | "B" | "C" | "F",
        "blocks": [...],   # must-fix issues that should prevent sending
        "warnings": [...], # should-fix issues
        "passed": bool,    # False if any block-level issue found
      }
    """
    text = (subject + "\n" + body).lower()
    blocks: list[dict] = []
    warnings: list[dict] = []

    for flag in _QUALITY_FLAGS:
        if flag["id"] == "too_long":
            word_count = len(body.split())
            if word_count > flag["word_limit"]:
                entry = {
                    "id": flag["id"],
                    "label": flag["label"],
                    "tip": flag["tip"],
                    "detail": f"{word_count} words (limit {flag['word_limit']})",
                }
                warnings.append(entry)
            continue

        matched = [p for p in flag["patterns"] if p in text]
        if matched:
            entry = {
                "id": flag["id"],
                "label": flag["label"],
                "tip": flag["tip"],
                "detail": f"Found: {', '.join(matched[:3])}",
            }
            if flag["severity"] == "block":
                blocks.append(entry)
            else:
                warnings.append(entry)

    deductions = len(blocks) * 20 + len(warnings) * 5
    score = max(0, 100 - deductions)

    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 50:
        grade = "C"
    else:
        grade = "F"

    return {
        "score": score,
        "grade": grade,
        "blocks": blocks,
        "warnings": warnings,
        "passed": len(blocks) == 0,
    }

def run_bulk_outreach(
    db: Session,
    simulate: bool = True,
    custom_body: str | None = None,
    statuses: list[str] | None = None,
) -> dict:
    target_statuses = statuses or ["DISCOVERED", "QUALIFIED"]
    leads = db.query(B2BLead).filter(
        B2BLead.email.like("%@%"),
        B2BLead.status.in_(target_statuses)
    ).all()

    report = {
        "total_leads_found": len(leads),
        "emails_processed": 0,
        "emails_sent_real": 0,
        "emails_simulated": 0,
        "failed_leads": [],
        "details": []
    }

    zoho_password = os.getenv("ZOHO_APP_PASSWORD", "")
    is_smtp_available = bool(zoho_password and zoho_password != "your_zoho_app_password_here" and zoho_password.strip() != "")

    for lead in leads:
        if custom_body:
            if lead.contact_name:
                body = custom_body.replace("Hi {contact_name},", f"Hi {lead.contact_name},").replace("{contact_name}", lead.contact_name)
            else:
                body = custom_body.replace("Hi {contact_name},", "Hi,").replace("{contact_name}", "")
            body = body.replace("{company}", lead.company)
            subject = f"Premium Pantry Coffee Supply Proposal for {lead.company}"
        else:
            subject, body = generate_b2b_pitch_email(lead)
            
        email_obj = build_outreach_email(lead.email, lead.contact_name, lead.company, subject, body)
        
        sent_real = False
        error_msg = ""
        
        if is_smtp_available and not simulate:
            result_email = send_email(email_obj)
            if result_email.status == "sent":
                sent_real = True
            else:
                error_msg = result_email.error
        
        if sent_real:
            report["emails_sent_real"] += 1
            lead.status = "EMAIL_SENT"
            lead.recommended_action = "[OUTREACH SENT] Introduction email sent via Zoho. Awaiting reply or call booking."
            lead.qualification_notes = f"Outreach email successfully sent on {datetime.utcnow().strftime('%d %b %Y')}."
            lead.last_updated = datetime.utcnow()
        elif simulate or not is_smtp_available:
            report["emails_simulated"] += 1
            lead.status = "EMAIL_SENT"
            lead.recommended_action = "[OUTREACH SIMULATED] Introduction email drafted for Zoho sending. Awaiting reply."
            lead.qualification_notes = f"Outreach email generated and logged in pipeline (simulated delivery) on {datetime.utcnow().strftime('%d %b %Y')}."
            lead.last_updated = datetime.utcnow()
        else:
            report["failed_leads"].append({"company": lead.company, "error": error_msg})
            continue

        CRMTrackerService.recalculate_lead_score_and_action(lead)
        db.commit()

        # Log agent activity
        db.add(AgentLog(
            agent_name="B2B Outreach Agent",
            action="Sent Bulk B2B Outreach Email",
            timestamp=datetime.utcnow(),
            payload={
                "company": lead.company,
                "to_email": lead.email,
                "subject": subject,
                "real_delivery": sent_real,
                "body_snippet": body[:120]
            }
        ))
        db.commit()

        report["emails_processed"] += 1
        report["details"].append({
            "company": lead.company,
            "to_email": lead.email,
            "subject": subject,
            "body": body,
            "real_delivery": sent_real
        })

    return report


def reconcile_sent_emails_via_imap(db: Session) -> dict:
    from app.models.models import EmailDraft, B2BLead
    import imaplib
    import email as pyemail
    import logging
    from datetime import datetime, timedelta
    
    report = {
        "status": "success",
        "processed": 0,
        "matched": 0,
        "errors": []
    }
    
    sender_email = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    password = os.getenv("ZOHO_APP_PASSWORD", "")
    
    if not password or password == "your_zoho_app_password_here" or password.strip() == "":
        report["status"] = "error"
        report["errors"].append("ZOHO_APP_PASSWORD not set or placeholder.")
        return report
        
    try:
        # Connect to Zoho IMAP
        imap_host = "imap.zoho.in"
        mail = imaplib.IMAP4_SSL(imap_host, 993)
        mail.login(sender_email, password)
        
        # Select Sent folder
        status, folder_data = mail.select("Sent")
        if status != 'OK':
            status, folder_data = mail.select("[Zoho]/Sent")
            
        if status != 'OK':
            report["status"] = "error"
            report["errors"].append("Could not select Sent folder in Zoho IMAP.")
            return report
            
        # Search for recent messages in the last 7 days
        since_date = (datetime.utcnow() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'SINCE {since_date}')
        
        if status != 'OK':
            report["status"] = "error"
            report["errors"].append("Failed to search sent emails.")
            return report
            
        mail_ids = data[0].split()
        report["processed"] = len(mail_ids)
        
        # Fetch the Message-ID for each recent sent email
        for m_id in mail_ids[-50:]:  # Process up to last 50 emails
            status, msg_data = mail.fetch(m_id, '(BODY[HEADER.FIELDS (MESSAGE-ID DATE SUBJECT TO)])')
            if status != 'OK' or not msg_data:
                continue
                
            raw_headers = msg_data[0][1]
            if not raw_headers:
                continue
                
            msg = pyemail.message_from_bytes(raw_headers)
            msg_id_header = msg.get("Message-ID")
            if not msg_id_header:
                continue
                
            zoho_msg_id = msg_id_header.strip()
            
            # Find matching draft in DB
            draft = db.query(EmailDraft).filter(EmailDraft.zoho_message_id == zoho_msg_id).first()
            if draft:
                if draft.status != "SENT":
                    draft.status = "SENT"
                    draft.delivery_status = "PROVIDER_ACCEPTED"
                    draft.sent_at = datetime.utcnow()
                    
                    # Update lead status
                    lead = db.query(B2BLead).filter(B2BLead.id == draft.lead_id).first()
                    if lead and lead.status != "EMAIL_SENT":
                        lead.status = "EMAIL_SENT"
                        lead.last_updated = datetime.utcnow()
                        
                    report["matched"] += 1
                    print(f"Matched draft {draft.id} with Zoho Sent mail.")
                    
        db.commit()
        mail.close()
        mail.logout()
        
    except Exception as e:
        report["status"] = "error"
        report["errors"].append(str(e))
        logging.error(f"IMAP Reconciler error: {str(e)}")
        
    return report



def reconcile_inbound_replies_via_imap(db) -> dict:
    from app.models.models import B2BLead, LeadInteraction, EmailDraft
    from app.services.pipeline_tracker import track
    import imaplib
    import email as pyemail
    import logging
    import os
    import re
    from datetime import datetime, timedelta
    
    report = {
        "status": "success",
        "processed": 0,
        "matched": 0,
        "errors": []
    }
    
    sender_email = os.getenv("SENDER_EMAIL", "connect@purepantryprovisions.com")
    password = os.getenv("ZOHO_APP_PASSWORD", "")
    
    if not password or password == "your_zoho_app_password_here" or password.strip() == "":
        report["status"] = "error"
        report["errors"].append("ZOHO_APP_PASSWORD not set or placeholder.")
        return report
        
    try:
        imap_host = "imap.zoho.in"
        mail = imaplib.IMAP4_SSL(imap_host, 993)
        mail.login(sender_email, password)
        
        status, folder_data = mail.select("INBOX")
        if status != 'OK':
            report["status"] = "error"
            report["errors"].append("Could not select INBOX folder in Zoho IMAP.")
            return report
            
        # Search for recent messages in the last 7 days
        since_date = (datetime.utcnow() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'SINCE {since_date}')
        
        if status != 'OK':
            report["status"] = "error"
            report["errors"].append("Failed to search inbox emails.")
            return report
            
        mail_ids = data[0].split()
        report["processed"] = len(mail_ids)
        
        def classify_intent(text_content: str) -> str:
            text_lower = text_content.lower()
            if any(k in text_lower for k in ("sample", "samples", "tasting", "taste", "test kit", "sample kit")):
                return "SAMPLE_REQUEST"
            if any(k in text_lower for k in ("price", "pricing", "cost", "quote", "quotation", "rate", "rates", "discount")):
                return "PRICING_REQUEST"
            if any(k in text_lower for k in ("call me", "phone", "mobile", "number", "talk", "schedule", "meet", "meeting", "call later", "busy")):
                return "CALL_REQUEST"
            if any(k in text_lower for k in ("already buy", "current supplier", "existing supplier", "buying from")):
                return "EXISTING_SUPPLIER"
            if any(k in text_lower for k in ("not interested", "no thanks", "don't need", "unsubscribe", "remove", "stop emailing")):
                return "NOT_INTERESTED"
            if any(k in text_lower for k in ("wrong person", "not correct person", "not responsible")):
                return "WRONG_PERSON"
            if any(k in text_lower for k in ("no requirement", "no need", "don't require")):
                return "NO_REQUIREMENT"
            if any(k in text_lower for k in ("interest", "info", "details", "catalogue", "catalog")):
                return "INTERESTED"
            return "UNKNOWN"

        for m_id in mail_ids[-50:]:  # Process up to last 50 inbox emails
            status, msg_data = mail.fetch(m_id, '(RFC822)')
            if status != 'OK' or not msg_data:
                continue
                
            raw_email = msg_data[0][1]
            if not raw_email:
                continue
                
            msg = pyemail.message_from_bytes(raw_email)
            
            # Parse From address
            from_header = msg.get("From", "")
            email_match = re.search(r'[\w\.-]+@[\w\.-]+', from_header)
            if not email_match:
                continue
            sender_addr = email_match.group(0).lower().strip()
            
            # Skip if sender is ourselves
            if sender_addr == sender_email.lower().strip():
                continue
                
            # Find lead
            lead = db.query(B2BLead).filter(B2BLead.email.ilike(sender_addr)).first()
            if not lead:
                continue
                
            # Get body content
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if content_type == "text/plain" and "attachment" not in content_disposition:
                        try:
                            body = part.get_payload(decode=True).decode(errors="ignore")
                        except Exception as _exc:
                            # Swallowed on purpose — this path must not break the
                            # caller — but never silently: a failure with no name is
                            # how the category engine fell back for hours unnoticed.
                            _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
                        break
            else:
                try:
                    body = msg.get_payload(decode=True).decode(errors="ignore")
                except Exception as _exc:
                    # Swallowed on purpose — this path must not break the
                    # caller — but never silently: a failure with no name is
                    # how the category engine fell back for hours unnoticed.
                    _log.debug('suppressed: %s: %s', type(_exc).__name__, _exc)
                    
            intent = classify_intent(body or msg.get("Subject", ""))
            
            # Register interaction + pause sequence
            existing_inter = db.query(LeadInteraction).filter(
                LeadInteraction.lead_id == lead.id,
                LeadInteraction.method == "email",
                LeadInteraction.outcome == "replied",
                LeadInteraction.remark.like("Inbound email reply: %")
            ).first()
            
            if not existing_inter:
                before_status = lead.status
                lead.status = "REPLIED"
                lead.last_updated = datetime.utcnow()
                
                draft = db.query(EmailDraft).filter(EmailDraft.lead_id == lead.id).order_by(EmailDraft.id.desc()).first()
                if draft:
                    draft.reply_status = "REPLIED"
                    draft.replied_at = datetime.utcnow()
                    
                inter = LeadInteraction(
                    lead_id=lead.id,
                    method="email",
                    outcome="replied",
                    remark=f"Inbound email reply: {msg.get('Subject', '')} - Classified intent: {intent}",
                    occurred_at=datetime.utcnow(),
                    lead_owner="SYSTEM_IMAP"
                )
                db.add(inter)
                
                # Delete any open OutreachReminders to halt future follow-ups
                from app.models.models import OutreachReminder
                db.query(OutreachReminder).filter(
                    OutreachReminder.lead_id == lead.id,
                    OutreachReminder.status == "OPEN"
                ).delete()
                
                track(db, "EMAIL_REPLIED", lead_id=lead.id, actor="SYSTEM", channel="email",
                      before_status=before_status, after_status="REPLIED",
                      payload={"subject": msg.get("Subject", ""), "intent": intent, "body_snippet": (body or "")[:200]})
                      
                report["matched"] += 1
                print(f"IMAP Reply Sync: Lead {lead.company} replied! Paused outbound sequences.")
                
        db.commit()
        mail.close()
        mail.logout()
        
    except Exception as e:
        report["status"] = "error"
        report["errors"].append(str(e))
        logging.error(f"IMAP Inbox Reply Reconciler error: {str(e)}")
        
    return report
