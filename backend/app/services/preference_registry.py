r"""
Do-not-call preference registry — the scrub that must pass before a cold dial.

Why this file exists
--------------------
Until now nothing in this codebase checked a preference registry at all. A grep
for DND / TRAI / TCCCPR / NCPR returned zero hits. The internal `do_not_call`
flag only ever held people who had already told *us* to stop, which is a
suppression list of one business's own history, not a scrub.

What this is honest about
-------------------------
There is no free public API that answers "is this Indian number registered on
the DND preference registry". Access runs through the operator / DLT side. So
this module does NOT pretend to perform a live lookup. It loads a suppression
list that YOU supply — an export from your telecom partner, a DLT scrub result,
a manually maintained list, or all three concatenated — and it refuses to
authorise a cold call when no such list has been configured.

That refusal is the point. "We did not check" and "we checked and it was clear"
must not produce the same outcome, or the scrub is decorative.

Configure it
------------
    DND_SUPPRESSION_FILE=C:\path\to\dnd_suppression.txt

One number per line. Blank lines and lines starting with # are ignored, so an
operator export can be pasted in with its own header intact. Matching is on the
last 10 digits, so +91, 0-prefixes and spacing do not cause a miss.

An EMPTY file is a valid configuration and means "I have a registry and nobody
is on it". That is a deliberate, recorded decision. A MISSING file is not.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Iterable

# Load .env HERE rather than relying on some other module's import order to
# have done it first — see email_sender.py for the incident this pattern
# exists to prevent. This module fails CLOSED (an unconfigured registry
# refuses every cold call rather than allowing one), so the risk of the
# missing-load version of this bug is not a compliance breach — it is every
# cold call in a given process silently refusing with "no preference
# registry configured" while DND_SUPPRESSION_FILE sits correctly set in
# backend/.env, unnoticed for exactly the reason the "0 replies" email
# incident went unnoticed: a fail-safe with no alarm looks identical to
# a quiet day.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "..", ".env"))
except Exception as _e:
    print(f"[preference_registry] .env load skipped: {_e}")

_lock = threading.Lock()
_cache: set[str] | None = None
_cache_key: tuple[str, float, int] | None = None


# Shared, so the scrub list matches numbers exactly as every other module
# reads them. A registry that normalises differently from the dialler is a
# registry that fails to suppress.
from app.services.identity import digits_only as _digits


def registry_path() -> str:
    return (os.getenv("DND_SUPPRESSION_FILE", "") or "").strip()


def _load(path: str) -> set[str]:
    out: set[str] = set()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            d = _digits(line)
            if len(d) == 10:
                out.add(d)
    return out


def _suppressed() -> set[str]:
    """Cached by (path, mtime, size) so an updated list is picked up without a
    restart, and an unchanged one is not re-read on every dial."""
    global _cache, _cache_key
    path = registry_path()
    if not path or not os.path.exists(path):
        return set()
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    with _lock:
        if key != _cache_key:
            _cache = _load(path)
            _cache_key = key
        return _cache or set()


def status() -> tuple[bool, str]:
    """Is a registry configured and readable? Reported before any dial."""
    path = registry_path()
    if not path:
        return False, ("no preference registry configured. Set "
                       "DND_SUPPRESSION_FILE to a scrub list before cold "
                       "calling; an empty file is a valid answer, a missing "
                       "one is not.")
    if not os.path.exists(path):
        return False, f"DND_SUPPRESSION_FILE points at a file that does not exist: {path}"
    try:
        n = len(_suppressed())
    except OSError as exc:
        return False, f"preference registry unreadable: {exc}"
    return True, f"{n} suppressed number(s) loaded from {os.path.basename(path)}"


def is_suppressed(phone) -> bool:
    d = _digits(phone)
    return bool(d) and d in _suppressed()


def check(phone) -> tuple[bool, str]:
    """Fail-closed scrub. (allowed, reason).

    Refuses when no registry is configured — an unchecked number is not a
    cleared number.
    """
    ok, detail = status()
    if not ok:
        return False, detail
    d = _digits(phone)
    if len(d) != 10:
        return False, f"not a dialable 10-digit Indian number: {phone!r}"
    if d in _suppressed():
        return False, "number is on the do-not-call preference registry"
    return True, "cleared against the preference registry"


def add(numbers: Iterable[str]) -> int:
    """Append numbers to the configured registry (used when someone opts out
    on a call). Returns how many were newly written."""
    path = registry_path()
    if not path:
        raise RuntimeError("DND_SUPPRESSION_FILE is not configured")
    have = _suppressed()
    new = [d for d in (_digits(n) for n in numbers) if len(d) == 10 and d not in have]
    if new:
        with open(path, "a", encoding="utf-8") as fh:
            for d in new:
                fh.write(d + "\n")
    return len(new)
