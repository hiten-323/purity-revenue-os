"""
Pre-send wording gate (29 Oct 2026, Hiten's rules). Pure function, no I/O.

Blocks an outbound message when it:
  * offers a free / complimentary sample, tasting jar or sample kit, or
    otherwise offers or refers to sending samples ("A sample for ...?",
    "Shall I send sample kits?", "the sample we sent")
  * says "100%" anywhere (say "pure coffee" instead)
  * makes any GST claim (4 Nov 2026)
  * calls Bold or Ultra Blend, or the whole range, freeze-dried
    (Bold and Ultra Blend are agglomerated)
  * puts "no chicory" / "chicory-free" next to Ultra Blend (it contains chicory)

check(subject, body) -> (ok: bool, why: str)
"""
from __future__ import annotations

import re

_SAMPLE = [
    r"\b(free|complimentary|trial|tasting)\s+(\w+\s+){0,2}(samples?|jars?|kits?|packs?)\b",
    r"\bsample\s+(kits?|jars?|packs?|boxe?s?|sachets?)\b",
    r"\b(send|sending|sent|ship|courier|dispatch|offer|share)\s+(you\s+)?(a\s+|some\s+|the\s+|our\s+)?samples?\b",
    r"\ba\s+sample\s+for\b",
    r"\bsamples?\s+(we|i)\s+(sent|shared|couriered|dispatched)\b",
    r"\b(try|taste)\s+(a\s+)?samples?\b",
    r"\bsamples?\s+(is|are)\s+(free|on\s+us|complimentary)\b",
]
_SAMPLE_RX = [re.compile(p, re.I) for p in _SAMPLE]
_PCT_RX = re.compile(r"100\s*(%|percent|per\s*cent)", re.I)
# 4 Nov 2026 (Hiten "stop,fix all,resume", external audit: 48 sent emails had GST
# wording): no GST claim of any kind in outbound copy.
_GST_RX = re.compile(r"\bGST(IN)?\b|goods\s+and\s+services\s+tax", re.I)
_FD = r"freeze[\s-]*dried"
_RANGE = r"(all\s+(of\s+)?our|our\s+(entire|whole|full)?\s*range|entire\s+range|whole\s+range|full\s+range|every\s+(variant|variety|jar|blend)|all\s+(variants|varieties|blends|products|coffees|jars)|the\s+range)"
_SENT_SPLIT = re.compile(r"(?<=[.!?\n])\s+|\n")
_BOLD = re.compile(r"\bbold\b", re.I)
_ULTRA = re.compile(r"\bultra\s*blend\b", re.I)
_PRIMA = re.compile(r"\bprima\b", re.I)                    # 29 Oct re-audit: agglomerated
_VARIETY = re.compile(r"\bvariety\s*box\b", re.I)          # 29 Oct re-audit: contains chicory
_FD_RX = re.compile(_FD, re.I)
_RANGE_RX = re.compile(_RANGE, re.I)
_NOCHIC_RX = re.compile(r"\b(no|zero|without|free\s+of|free\s+from)\s+(added\s+)?chicory\b|chicory[\s-]*free", re.I)


def _sentences(text: str):
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if s:
            yield s


def check(subject: str, body: str) -> tuple[bool, str]:
    text = f"{subject or ''}\n{body or ''}"
    for rx in _SAMPLE_RX:
        m = rx.search(text)
        if m:
            return False, f"sample offer ({m.group(0)!r}); samples are never offered"
    m = _PCT_RX.search(text)
    if m:
        return False, f"'{m.group(0)}' is banned; say 'pure coffee'"
    m = _GST_RX.search(text)
    if m:
        return False, f"GST claim ({m.group(0)!r}) is not allowed in outbound copy"
    for s in _sentences(text):
        if _FD_RX.search(s):
            if _BOLD.search(s):
                return False, "Bold called freeze-dried (it is agglomerated)"
            if _ULTRA.search(s):
                return False, "Ultra Blend called freeze-dried (it is agglomerated)"
            if _RANGE_RX.search(s):
                return False, "whole range called freeze-dried (Bold and Ultra Blend are agglomerated)"
        if _ULTRA.search(s) and _NOCHIC_RX.search(s):
            return False, "'no chicory' next to Ultra Blend (it contains chicory)"
        if _VARIETY.search(s) and _NOCHIC_RX.search(s):
            return False, "'no chicory' next to the Variety Box (it contains chicory)"
        if _FD_RX.search(s) and _PRIMA.search(s):
            return False, "Prima called freeze-dried (it is agglomerated)"
    return True, "ok"
