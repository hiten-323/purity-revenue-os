"""
The 2026-09-15/16 incident: one number on 1,166 of 1,858 leads (63%).

ROOT CAUSE, established by fetching the real page, not by inspection:
Bing's search-results template carries

    <meta property="fb:app_id" content="3732605936979161" />

on every single results page, regardless of query. "7326059369" is
characters 2-11 of that 16-digit id. The company name always sits in
<title>/og:title a few hundred characters away in the same <head> block, so
_phones_near_company's proximity window attributed Bing's own static
template to whichever company happened to be searched -- cafes, hospitals,
government offices, industrial firms, anything -- because the number has
nothing to do with any business.

The fix strips <head> before the proximity search runs, for every caller,
not a denylist on this one Facebook app id: a <meta>/<script>/<style> tag is
markup a visitor never reads, and none of it is ever a real phone number.

This snippet is a trimmed, real capture of Bing's <head> block -- not a
fabricated example -- with the surrounding page shortened. It is used
instead of a live fetch so the test is deterministic and has no network
dependency in CI.
"""
from __future__ import annotations

from app.services.contact_enricher import _phones_near_company, _STRIP_HEAD_RE

# A trimmed, real capture of the structure that caused the incident.
_BING_HEAD_SNIPPET = """<!DOCTYPE html><html><script nonce="x"></script><head><!--pc--><title>Acme Traders Ludhiana phone contact - Bing</title><meta content="text/html; charset=utf-8" http-equiv="Content-Type" /><meta property="og:title" content="Acme Traders Ludhiana phone contact - Bing" /><meta property="og:description" content="Intelligent search from Bing." /><meta property="og:site_name" content="Bing" /><meta property="fb:app_id" content="3732605936979161" /><meta property="og:image" content="http://www.bing.com/sa/simg/facebook_sharing_5.png" /></head><body><div>Acme Traders, Ludhiana. Call us on 98765 43210 for orders.</div></body></html>"""


def test_the_facebook_app_id_is_never_extracted_as_a_phone():
    """The exact failure. Company name sits right next to fb:app_id in
    <head> -- if this returns the app-id fragment, the incident is back."""
    result = _phones_near_company(_BING_HEAD_SNIPPET, "Acme Traders")
    assert "7326059369" not in result, (
        "the Facebook app id from <head> was extracted as a phone number "
        "-- this is the exact 2026-09-15/16 incident recurring")


def test_a_real_body_phone_near_the_company_name_still_works():
    """The fix must not go blind. A number that legitimately appears in
    visible body content, near the company's name, is still real evidence
    and must still be found."""
    result = _phones_near_company(_BING_HEAD_SNIPPET, "Acme Traders")
    assert any("98765" in p or "9876543210" in p for p in result), (
        f"a real, visible phone near the company name was not found: {result}"
    )


def test_head_is_actually_removed_not_silently_unchanged():
    """A regression test that passes only because the strip silently did
    nothing (e.g. a corrupted escape turning \\b into a literal control
    byte, which is exactly what broke this fix the first time it was
    written) would hide the bug instead of catching it. Assert the strip
    has a real, non-trivial effect."""
    stripped = _STRIP_HEAD_RE.sub(" ", _BING_HEAD_SNIPPET)
    assert len(stripped) < len(_BING_HEAD_SNIPPET) - 200, (
        "the <head> strip removed little or nothing -- the regex is not "
        "matching, the same failure mode that let this incident recur once "
        "already during development")
    assert "fb:app_id" not in stripped
    assert "3732605936979161" not in stripped


def test_no_meta_or_script_boilerplate_survives_across_common_shapes():
    """A handful of other <head> shapes seen across the real scrapers
    (self-closing meta, script with attributes, multiple metas) -- not
    exhaustive, but enough to catch a regex that only worked on one exact
    snippet."""
    shapes = [
        '<head><meta name="x" content="1234567890"></head><body>Beta Co 99999 88888</body>',
        "<head><script src=\"a.js\" nonce='y'></script><meta property='fb:app_id' content='1112223334445556'></head><body>Gamma Ltd call 77776 66665</body>",
        "<HEAD><TITLE>Delta</TITLE><META content='9998887776'></HEAD><body>Delta Traders 55554 44443</body>",
    ]
    for html in shapes:
        stripped = _STRIP_HEAD_RE.sub(" ", html)
        assert "<meta" not in stripped.lower()
        assert "<script" not in stripped.lower()


def test_quarantine_recognises_a_shared_number_not_just_placeholder_patterns():
    """The 2026-09-15/16 incident wrote a real-looking, well-formed Indian
    mobile number onto 1,166 leads. Neither _is_placeholder_phone (digit-run
    patterns like 8888888888) nor _is_id_derived_phone (number ending in the
    row's own id) can catch it -- it isn't lexically wrong, only socially
    wrong: no real subscriber's number belongs to hundreds of unrelated
    businesses. quarantine_fabricated must also check
    is_shared_across_many_leads, or the cleanup endpoint quietly misses the
    exact incident it exists to clean up.
    """
    import inspect

    from app.api import endpoints

    src = inspect.getsource(endpoints.quarantine_fabricated)
    assert "is_shared_across_many_leads" in src, (
        "quarantine_fabricated does not check for a phone number shared "
        "across many leads -- it would leave the 2026-09-15/16 incident's "
        "1,166 contaminated leads uncleaned")
