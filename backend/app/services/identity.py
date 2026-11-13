r"""
One definition of what a phone number and an email address ARE.

Why this module exists
----------------------
A sweep for duplicated definitions found the same concepts implemented
repeatedly, and — the part that matters — the copies had drifted:

    normalise_msisdn   2 implementations, and they DISAGREED:
                         "09876543210" -> 919876543210  (whatsapp_aisensy)
                                       -> 09876543210   (whatsapp_gateway)
                       The second is not a dialable destination. A test in this
                       repo already said "two normalisers that disagree send to
                       two different numbers"; it asserted only one of them.

    digits_only        6 implementations across services and scripts

    FREE_MAIL          3 sets, all different. scrapling_harvester knew about
                       icloud/protonmail/ymail; the others did not.

    ROLE_PREFIX        2 sets differing by 8 entries — trust_promoter knew
                       reservations@, bookings@, events@, enquiries@ and
                       contact_trust did not. Those are exactly the role
                       addresses a hotel or restaurant publishes, so the two
                       modules reached different trust conclusions about the
                       priority segment.

Duplication is not the defect; drift is. Two copies agree on the day they are
written and diverge on the day one is improved, and nothing fails when they do.

This module is a LEAF: it imports nothing from the application, so anything may
import it without a cycle and without dragging in httpx or SQLAlchemy.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------- phone --

INDIA_CC = "91"

# Two email patterns, because there are two jobs and conflating them is a real
# bug in both directions: an anchored pattern finds nothing in a page, and an
# unanchored one calls "mail us at x@y.com today" a valid address.
#
# EXTRACTING addresses out of page text — unanchored.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# VALIDATING that one string IS an address — whole string only. email_verifier
# had this anchored form and the harvesters had the loose one; that difference
# was correct and is preserved rather than flattened.
EMAIL_EXACT_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Indian mobiles in both published forms: ten contiguous digits, or 5+5 split
# by a space or dash. The three previous copies each handled a subset — the
# harvester accepted the split form, the enrichment engine did not — so the
# same page yielded different numbers depending on which module read it.
# 27 Oct 2026: digit boundaries. Without them the first alternative matched a
# 10-digit window INSIDE a longer digit run -- facebook.com/106106944413135
# yielded "6106944413", the source of the dead +91 6060/6106/6144/6968 numbers.
# (?<=\b0) keeps a leading trunk 0 ("09876543210") matching as before.
PHONE_RE = re.compile(
    r"(?:(?<=\b0)|(?<!\d))(?:\+?91[\s\-]?)?(?:[6-9]\d{4}[\s\-]?\d{5}|\b[6-9]\d{9}\b)(?!\d)")


def digits_only(value) -> str:
    """The last 10 digits — the part that identifies an Indian subscriber.

    Used for MATCHING, because the same number is stored as +91-98765-43210,
    919876543210 and 9876543210 in different rows, and an exact-string match
    silently finds nothing and reads as "no such lead".
    """
    d = re.sub(r"\D", "", str(value or ""))
    return d[-10:] if len(d) >= 10 else d


def msisdn(value) -> str:
    """Country-coded digits, no plus — what a provider wants as a destination.

    Used for SENDING. Distinct from digits_only on purpose: matching wants the
    bare subscriber number, dialling wants the country code. Conflating them is
    how "09876543210" reached a provider as a destination.
    """
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) == 11 and d.startswith("0"):
        d = d[1:]                       # national trunk prefix
    if len(d) == 10:
        d = INDIA_CC + d
    return d


def is_landline(value) -> bool:
    """A number that reaches a desk, not a handset.

    Indian mobiles are 10 digits starting 6-9. Anything else that still looks
    like a number is an STD-coded landline or a toll-free line: perfectly
    callable, and unreachable by WhatsApp.

    This deliberately does NOT use digits_only(). Taking the last 10 digits
    throws away the very prefix that identifies the number — "+91 1800 891
    0001" becomes "8008910001", which starts with 8 and reads as a mobile. A
    first attempt at unifying these helpers did exactly that and an existing
    test caught it. Strip the country code and trunk zeros from the FRONT, then
    judge what is left.
    """
    d = re.sub(r"\D", "", str(value or ""))
    if d.startswith(INDIA_CC) and len(d) > 10:
        d = d[2:]
    d = d.lstrip("0")
    return bool(d) and not (len(d) == 10 and d[0] in "6789")


def is_mobile(value) -> bool:
    return not is_landline(value)


# 8 Oct 2026 (founder): the AI call queue selects ONLY numbers the voice service
# can actually dial. Exact mirror of ai-voice-agent/src/lib/phone.js
# normalizePhone() + isIndianMobile() (/^\+91[6-9]\d{9}$/), which
# trigger-livekit-call.js applies before SIP ("PRE_RING_FAILURE: not a valid
# Indian mobile for Plivo"). Keep the two in step. Landline-only leads are not
# changed in any way; they simply are not picked for calls (email unaffected).
_VOICE_E164_RE = re.compile(r"[1-9]\d{6,14}")
_VOICE_IN_MOBILE_RE = re.compile(r"\+91[6-9]\d{9}")


def voice_e164(raw) -> str | None:
    """Python port of the voice service's normalizePhone(): E.164 or None."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"[\s\-().]", "", s)
    if s.startswith("+"):
        d = s[1:]
        return "+" + d if _VOICE_E164_RE.fullmatch(d) else None
    if s.startswith("0"):
        s = s[1:]
    if s.startswith("91") and len(s) == 12 and s[2:3] in tuple("6789"):
        return "+91" + s[2:]
    if len(s) == 10 and s[0] in "6789":
        return "+91" + s
    if len(s) == 10:
        return None
    if _VOICE_E164_RE.fullmatch(s) and len(s) > 10:
        return "+" + s
    return None


def is_voice_dialable_mobile(raw) -> bool:
    """True only for a number the voice service will dial: +91 then 10 digits starting 6-9."""
    e = voice_e164(raw)
    return bool(e and _VOICE_IN_MOBILE_RE.fullmatch(e))


# 27 Oct 2026 (Hiten, 15:13 IST: "i want you to call their landlines"). Mirror
# of ai-voice-agent/src/lib/phone.js normalizeVoiceDialable(). A valid landline
# is geographic: STD code + subscriber = 10 national digits (after dropping
# +91/91 or a trunk 0), first digit 1-5. Toll-free / premium / non-geographic
# (1800, 1860, 1900) are never dialled. Keep the two in step.
NON_GEOGRAPHIC_PREFIXES = ("1800", "1860", "1900")
_VOICE_IN_LANDLINE_RE = re.compile(r"\+91[1-5]\d{9}")


def indian_national(raw) -> str | None:
    """National significant digits of an Indian number, or None."""
    if not raw:
        return None
    s = re.sub(r"[\s\-().]", "", str(raw).strip())
    if not s:
        return None
    if s.startswith("+"):
        if not s.startswith("+91"):
            return None
        s = s[3:]
    elif s.startswith("91") and len(s) == 12:
        s = s[2:]
    if s.startswith("0"):
        s = s[1:]
    return s if s.isdigit() else None


def is_non_geographic(raw) -> bool:
    n = indian_national(raw)
    return bool(n) and n.startswith(NON_GEOGRAPHIC_PREFIXES)


def voice_dialable_e164(raw) -> str | None:
    """+91XXXXXXXXXX for a dialable Indian mobile or geographic landline, else None."""
    n = indian_national(raw)
    if not n or len(n) != 10:
        return None
    e = "+91" + n
    if _VOICE_IN_MOBILE_RE.fullmatch(e):
        return e
    if _VOICE_IN_LANDLINE_RE.fullmatch(e) and not n.startswith(NON_GEOGRAPHIC_PREFIXES):
        return e
    return None


def is_voice_dialable(raw) -> bool:
    """True for a number the voice service will dial: Indian mobile or geographic landline."""
    return voice_dialable_e164(raw) is not None


# Promoted from app/api/endpoints.py's private _is_placeholder_phone /
# _is_id_derived_phone (2026-07-17 audit: 67 leads found live carrying
# id-derived fake numbers, e.g. +91-98765-00156 on lead 156). Those detectors
# were only ever wired into a manual, on-demand cleanup endpoint
# (quarantine_fabricated) — never into the actual calling eligibility gate,
# so a fabricated number introduced after the last manual run would still
# pass may_place_ai_call() and could be dialed. Moved here so both the
# cleanup endpoint and the calling gate read the same definition, the same
# reason every other phone/email rule in this module was consolidated: two
# copies agree on the day they're written and silently disagree later.
# 15 Oct 2026 (founder-approved, Hiten): exact numbers exempt from the
# repeated-digit check only. Both are published on Nestle Professional's
# distributor locator (https://www.nestleprofessional.in/distributor-locator):
#   9999975792 Khandelwal food, Moradabad (lead 2281)
#   9918977777 Sai International, Lucknow (lead 2300)
# Every other gate (DNC, hours, mobile-only, SIP_INVALID, ...) still applies.
FABRICATED_PATTERN_EXEMPT = frozenset({"9999975792", "9918977777"})


def is_fabricated_pattern(value) -> bool:
    """Digit-run / sequence placeholders: 8888888888, 9999999999, 123456, 9876543210…"""
    if not value:
        return False
    digits = re.sub(r"\D", "", str(value))
    core = digits[-10:] if len(digits) >= 10 else digits
    if core in FABRICATED_PATTERN_EXEMPT:
        return False
    if len(set(core)) <= 2:
        return True
    if core in ("9876543210", "0123456789", "1234567890", "0987654321"):
        return True
    return bool(re.search(r"(123456|234567|345678|456789|987654|876543|765432|654321|543210|00000|11111|22222|33333|44444|55555|66666|77777|88888|99999)", core))


def is_id_derived_phone(value, record_id) -> bool:
    """A number mechanically generated from the record's own id (e.g. the
    phone ends in the row id zero-padded to 5 digits). The odds of a real
    phone number ending in the exact zero-padded id of the row it's stored
    on are effectively zero, so this is a safe, specific signal — unlike
    is_fabricated_pattern, it can't false-positive on a genuine number."""
    if not value or record_id is None:
        return False
    digits = re.sub(r"\D", "", str(value))
    tail = digits[-5:] if len(digits) >= 5 else digits
    return tail == str(record_id).zfill(5)


# Threshold for founder_call_pipeline.is_shared_across_many_leads. Kept here
# (a plain int, no import needed) rather than there, so every module that
# already imports identity's other constants sees this one too.
#
# Found 2026-09-15: a still-unroot-caused process was silently overwriting
# freshly-created leads' phone numbers with +91-73260-59369 within ~2-10
# minutes of creation — already present on 150+ otherwise-unrelated leads
# (cafes, hospitals, government offices, industrial firms) in this database.
# Neither is_fabricated_pattern nor is_id_derived_phone catches it: the
# number is a plausible, well-formed Indian mobile number: the tell is
# social, not lexical, and only visible by looking at how many DIFFERENT
# businesses supposedly answer it. The check itself lives in
# founder_call_pipeline, not here — it needs B2BLead, and this module must
# stay a leaf (test_identity_imports_nothing_from_the_app) so every service
# that already depends on identity can keep doing so without a cycle. A
# phone shared by a handful of leads (e.g. a shared reception desk) is not
# impossible, so the threshold is set well below the observed 150+, not at 2.
SHARED_PHONE_THRESHOLD = 5


# --------------------------------------------------------------- email --

# Union of every copy found. A superset is the safe direction: a domain wrongly
# treated as free mail costs one cautious trust decision, while a free-mail
# domain treated as a company domain grants sending permission it should not.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com",
    "hotmail.com", "outlook.com", "live.com", "msn.com",
    "rediffmail.com", "rediff.com",
    "icloud.com", "me.com",
    "protonmail.com", "proton.me",
    "zoho.com", "aol.com", "gmx.com", "mail.com",
})

# Union of both copies. These are addresses a business publishes for a FUNCTION
# rather than a person — the hotel/restaurant ones (reservations, bookings,
# events) came from trust_promoter and were missing from contact_trust.
ROLE_PREFIX = frozenset({
    "info", "support", "contact", "admin", "hello", "sales", "care",
    "enquiry", "enquiries", "inquiry", "inquiries",
    "booking", "bookings", "reservation", "reservations",
    "events", "feedback", "suggestion", "suggestions",
    "office", "mail", "help", "team", "general",
})


def split_address(address: str) -> tuple[str, str]:
    """(local_part, domain), lowercased. ("", "") if it is not an address."""
    a = (address or "").strip().lower()
    if "@" not in a:
        return "", ""
    lp, _, dom = a.partition("@")
    return lp.strip(), dom.strip()


def is_free_mail(address: str) -> bool:
    return split_address(address)[1] in FREE_MAIL


def is_role_address(address: str) -> bool:
    return split_address(address)[0] in ROLE_PREFIX


def is_free_role_address(address: str) -> bool:
    """info@gmail.com — a function address on a consumer mailbox.

    Neither half is damning alone: info@thecafe.in is a normal business
    address, and rahul@gmail.com is a normal person. Both together is neither.
    """
    lp, dom = split_address(address)
    return bool(lp) and dom in FREE_MAIL and lp in ROLE_PREFIX
