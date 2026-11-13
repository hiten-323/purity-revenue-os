"""Supplier/vendor guard (24 Oct 2026, founder-approved).

Suppliers (packers, sachet makers, packaging, contract manufacturers) are never
called, emailed or WhatsApped by the B2B revenue pipeline. Fails OPEN: a missing
or broken suppliers.json returns (False, ...) so outreach never crashes.
"""
import json
import os
import re

_PATH = os.getenv("SUPPLIERS_JSON", os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "suppliers.json")))
_cache = {"mtime": None, "data": None}
_FREE = {"gmail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "hotmail.com", "outlook.com",
         "rediffmail.com", "live.com", "icloud.com", "ymail.com", "aol.com", "msn.com"}
_DEFAULT_KW = ["packaging", "sachet", "contract manufactur", "packer"]
_SUFFIX = r"\b(pvt|private|ltd|limited|llp|inc|co|company|india|industries|enterprises|the)\b"


def _norm(n):
    n = re.sub(r"\(.*?\)", "", (n or "").lower())
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = re.sub(_SUFFIX, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _load():
    try:
        m = os.path.getmtime(_PATH)
        if _cache["mtime"] != m:
            with open(_PATH, encoding="utf-8") as f:
                d = json.load(f)
            _cache["data"] = {
                "domains": {x.lower() for x in d.get("domains", [])} - _FREE,
                "emails": {x.lower() for x in d.get("emails", [])},
                "phones": set(d.get("phones", [])),
                "names": set(d.get("names", [])),
                "keywords": [k.lower() for k in d.get("keywords", _DEFAULT_KW)],
            }
            _cache["mtime"] = m
        return _cache["data"]
    except Exception:  # noqa: BLE001 - fail open
        return None


def is_supplier(lead):
    """Return (True, reason) if the lead is a known supplier/vendor."""
    try:
        d = _load()
        name = getattr(lead, "company", "") or ""
        cat = " ".join(str(getattr(lead, a, "") or "") for a in ("industry", "division", "category"))
        kws = d["keywords"] if d else _DEFAULT_KW
        low = (name + " " + cat).lower()
        for k in kws:
            if k in low:
                return True, "supplier keyword '%s'" % k
        if not d:
            return False, "supplier list unavailable"
        email = (getattr(lead, "email", "") or "").strip().lower()
        if email:
            if email in d["emails"]:
                return True, "supplier email"
            dom = email.split("@")[-1]
            if dom not in _FREE and dom in d["domains"]:
                return True, "supplier domain"
        web = (getattr(lead, "website", "") or "").lower()
        if web:
            w = re.sub(r"^(https?://)?(www\.)?", "", web).split("/")[0]
            if w and w in d["domains"]:
                return True, "supplier domain"
        for a in ("phone", "whatsapp_number"):
            dg = re.sub(r"\D", "", str(getattr(lead, a, "") or ""))
            if len(dg) >= 10 and dg[-10:] in d["phones"]:
                return True, "supplier phone"
        n = _norm(name)
        if n and n in d["names"]:
            return True, "supplier name"
        return False, ""
    except Exception:  # noqa: BLE001 - fail open
        return False, "supplier guard error"
