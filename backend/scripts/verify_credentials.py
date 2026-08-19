"""
Post-rotation credential verification.

Run AFTER rotating, against the .env the live process actually loads. Proves
each new credential works before the PM2 cutover, so a bad key is found here
rather than by a silent outreach failure at 2am.

NEVER prints a secret. Each check reports PASS/FAIL, the length, and a short
SHA-256 fingerprint — enough to confirm a value CHANGED without revealing it.

Read-only. Sends no email, no WhatsApp, places no order, writes nothing.

  exit 0  every configured credential verified
  exit 1  at least one failed or is missing
"""
from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

ENV_PATH = os.path.join(BACKEND, ".env")

# What the FILE says, read independently of the process environment.
def _file_values(path: str) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            t = raw.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


FILE_ENV = _file_values(ENV_PATH)

try:
    from dotenv import load_dotenv
    # NOT override=True on purpose: this must see exactly what the application
    # sees. python-dotenv leaves an existing OS variable in place, so if one
    # shadows the file the app uses the OS value and so must this check. The
    # shadow report below is what makes that visible instead of silent.
    load_dotenv(ENV_PATH)
except Exception:
    pass

results: list[tuple[str, bool, str]] = []


def fp(v: str) -> str:
    """Fingerprint, not the value: proves rotation happened, reveals nothing."""
    return hashlib.sha256(v.encode()).hexdigest()[:12]


def record(name: str, ok: bool, detail: str, value: str | None = None):
    if value:
        detail = f"{detail}  [len={len(value)} fp={fp(value)}]"
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + f"{name:26s} {detail}")


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


# ── 0. Is anything shadowing the .env? ───────────────────────────────────
# A rotated key in .env is inert if a stale OS environment variable holds the
# old value: load_dotenv() does not override an existing variable, so every
# process keeps using the old credential. AISENSY_API_KEY was found set as a
# Windows User variable with the pre-rotation value while .env already held the
# new one — the rotation looked done and changed nothing.
_shadowed = []
for _k, _fileval in FILE_ENV.items():
    _live = os.getenv(_k)
    if _live is not None and _fileval and _live != _fileval:
        _shadowed.append(_k)

print("POST-ROTATION CREDENTIAL VERIFICATION")
print("(read-only probes; no message is sent and no secret is printed)\n")

# ── 1. Cerebras ──────────────────────────────────────────────────────────
k = env("CEREBRAS_API_KEY")
if not k:
    record("CEREBRAS_API_KEY", False, "missing from .env")
else:
    try:
        import httpx
        r = httpx.get("https://api.cerebras.ai/v1/models",
                      headers={"Authorization": f"Bearer {k}"}, timeout=20)
        record("CEREBRAS_API_KEY", r.status_code == 200,
               f"GET /v1/models -> {r.status_code}", k)
    except Exception as e:
        record("CEREBRAS_API_KEY", False, f"{e.__class__.__name__}", k)

# ── 2. AiSensy ───────────────────────────────────────────────────────────
k = env("AISENSY_API_KEY")
if not k:
    record("AISENSY_API_KEY", False, "missing from .env")
else:
    try:
        import httpx
        # Unauthenticated -> 401. Any non-401 means the key was accepted at
        # the auth layer, which is what we are proving here.
        r = httpx.get("https://backend.aisensy.com/direct-apis/t1/users",
                      headers={"Authorization": f"Bearer {k}"}, timeout=20)
        record("AISENSY_API_KEY", r.status_code != 401,
               f"auth probe -> {r.status_code} (401 would mean rejected)", k)
    except Exception as e:
        record("AISENSY_API_KEY", False, f"{e.__class__.__name__}", k)

# ── 3. Zoho SMTP ─────────────────────────────────────────────────────────
k = env("ZOHO_APP_PASSWORD")
sender = env("SENDER_EMAIL") or "connect@purepantryprovisions.com"
if not k:
    record("ZOHO_APP_PASSWORD", False, "missing from .env")
else:
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.zoho.in", 465, context=ctx, timeout=25) as s:
            s.login(sender, k)          # login only; nothing is sent
        record("ZOHO_APP_PASSWORD", True, f"SMTP login OK as {sender}", k)
    except smtplib.SMTPAuthenticationError:
        record("ZOHO_APP_PASSWORD", False, "SMTP auth REJECTED", k)
    except Exception as e:
        record("ZOHO_APP_PASSWORD", False, f"{e.__class__.__name__}", k)

# ── 4. Google Maps ───────────────────────────────────────────────────────
k = env("GOOGLE_MAPS_API_KEY")
if not k:
    record("GOOGLE_MAPS_API_KEY", False, "missing from .env")
else:
    try:
        import httpx
        r = httpx.get("https://maps.googleapis.com/maps/api/geocode/json",
                      params={"address": "Abohar, Punjab", "key": k}, timeout=20)
        st = (r.json() or {}).get("status", "?")
        record("GOOGLE_MAPS_API_KEY", st in ("OK", "ZERO_RESULTS"),
               f"geocode status={st}", k)
    except Exception as e:
        record("GOOGLE_MAPS_API_KEY", False, f"{e.__class__.__name__}", k)

# ── 5. Shopify Admin token ───────────────────────────────────────────────
k = env("SHOPIFY_TOKEN")
store = env("SHOPIFY_STORE") or "55hd0v-ff.myshopify.com"
if not k:
    record("SHOPIFY_TOKEN", False, "missing from .env")
else:
    try:
        import httpx
        r = httpx.get(f"https://{store}/admin/api/2024-10/shop.json",
                      headers={"X-Shopify-Access-Token": k}, timeout=20)
        record("SHOPIFY_TOKEN", r.status_code == 200,
               f"GET shop.json -> {r.status_code}", k)
    except Exception as e:
        record("SHOPIFY_TOKEN", False, f"{e.__class__.__name__}", k)

# ── 6/7. Shared webhook secrets ──────────────────────────────────────────
# These are verified by the SENDER, so there is no endpoint to ask. What can be
# checked is that a value exists, is not a placeholder, and is long enough that
# an HMAC over it is worth anything.
for name in ("SHOPIFY_WEBHOOK_SECRET", "WHATSAPP_WEBHOOK_SECRET"):
    v = env(name)
    if not v:
        record(name, False, "missing from .env — webhook auth cannot succeed")
        continue
    weak = v.lower() in ("changeme", "secret", "test", "your_secret_here") \
        or v.lower().startswith("your_")
    record(name, (not weak) and len(v) >= 16,
           "placeholder value" if weak else
           ("too short (<16 chars)" if len(v) < 16 else "present"), v)

# ── 8. No credential may be reused for two purposes ──────────────────────
# WHATSAPP_WEBHOOK_SECRET was found holding the SAME value as AISENSY_API_KEY.
# That is two failures in one: rotating the API key silently breaks webhook
# auth, and the webhook secret is shared with a third party for verification
# while the API key can SEND messages — so leaking the verifier leaks the
# sender. They must be independently generated values.
pairs = [("AISENSY_API_KEY", "WHATSAPP_WEBHOOK_SECRET"),
         ("SHOPIFY_TOKEN", "SHOPIFY_WEBHOOK_SECRET"),
         ("ZOHO_APP_PASSWORD", "CEREBRAS_API_KEY")]
for a, b in pairs:
    va, vb = env(a), env(b)
    if va and vb and va == vb:
        record(f"{a} != {b}", False,
               "SAME VALUE reused for two purposes — rotate both to "
               "independently generated secrets")
    elif va and vb:
        record(f"{a} != {b}", True, "distinct values")

# ── 9. Shadowing is a failure in its own right ───────────────────────────
for _k in _shadowed:
    record(f"{_k} (.env vs OS env)", False,
           "an OS environment variable overrides .env — the rotated value in "
           "the file is NOT what the application will use. Remove the OS "
           "variable, then reopen the shell.")
if not _shadowed:
    record("no OS env shadows .env", True, "file values are what the app sees")

failed = [n for n, ok, _ in results if not ok]
print("\n" + "=" * 64)
if failed:
    print(f"FAILED {len(failed)}/{len(results)}: {', '.join(failed)}")
    print("Do NOT cut PM2 over until these pass.")
    sys.exit(1)
print(f"ALL {len(results)} CREDENTIALS VERIFIED — safe to proceed to cutover")
sys.exit(0)
