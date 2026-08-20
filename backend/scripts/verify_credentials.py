"""
Post-rotation credential verification.

NEVER prints a secret. exit 0 = all configured credentials verified.
Discovery uses OpenStreetMap only — Google Maps is not required.
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
    load_dotenv(ENV_PATH)
except Exception:
    pass

results: list[tuple[str, bool, str]] = []


def fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:12]


def record(name: str, ok: bool, detail: str, value: str | None = None):
    if value:
        detail = f"{detail}  [len={len(value)} fp={fp(value)}]"
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + f"{name:26s} {detail}")


def env(name: str) -> str:
    return (os.getenv(name) or "").strip()


_shadowed = []
for _k, _fileval in FILE_ENV.items():
    _live = os.getenv(_k)
    if _live is not None and _fileval and _live != _fileval:
        _shadowed.append(_k)

print("POST-ROTATION CREDENTIAL VERIFICATION")
print("(read-only probes; no message is sent and no secret is printed)\n")

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

k = env("AISENSY_API_KEY")
if not k:
    record("AISENSY_API_KEY", False, "missing from .env")
else:
    try:
        import httpx
        r = httpx.get("https://backend.aisensy.com/direct-apis/t1/users",
                      headers={"Authorization": f"Bearer {k}"}, timeout=20)
        record("AISENSY_API_KEY", r.status_code != 401,
               f"auth probe -> {r.status_code} (401 would mean rejected)", k)
    except Exception as e:
        record("AISENSY_API_KEY", False, f"{e.__class__.__name__}", k)

k = env("ZOHO_APP_PASSWORD")
sender = env("SENDER_EMAIL") or "connect@purepantryprovisions.com"
if not k:
    record("ZOHO_APP_PASSWORD", False, "missing from .env")
else:
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.zoho.in", 465, context=ctx, timeout=25) as s:
            s.login(sender, k)
        record("ZOHO_APP_PASSWORD", True, f"SMTP login OK as {sender}", k)
    except smtplib.SMTPAuthenticationError:
        record("ZOHO_APP_PASSWORD", False, "SMTP auth REJECTED", k)
    except Exception as e:
        record("ZOHO_APP_PASSWORD", False, f"{e.__class__.__name__}", k)

# Discovery maps = OpenStreetMap only (Google removed from discovery path)
try:
    import httpx
    ua = env("OSM_USER_AGENT") or (
        "PurityRevenueOS/1.0 (credential-check; contact=connect@purepantryprovisions.com)")
    r = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": "Abohar, India", "format": "json", "limit": 1},
        headers={"User-Agent": ua},
        timeout=25,
    )
    hits = r.json() if r.status_code == 200 else []
    ok = r.status_code == 200 and isinstance(hits, list) and len(hits) > 0
    record("OSM_DISCOVERY (Nominatim)", ok,
           f"geocode Abohar -> HTTP {r.status_code} hits={len(hits) if isinstance(hits, list) else 0}")
except Exception as e:
    record("OSM_DISCOVERY (Nominatim)", False, f"{e.__class__.__name__}")

record("GOOGLE_MAPS_API_KEY", True,
       "not used for discovery (OSM only)")

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

pairs = [("AISENSY_API_KEY", "WHATSAPP_WEBHOOK_SECRET"),
         ("SHOPIFY_TOKEN", "SHOPIFY_WEBHOOK_SECRET"),
         ("ZOHO_APP_PASSWORD", "CEREBRAS_API_KEY")]
for a, b in pairs:
    va, vb = env(a), env(b)
    if va and vb and va == vb:
        record(f"{a} != {b}", False,
               "SAME VALUE reused for two purposes — rotate both")
    elif va and vb:
        record(f"{a} != {b}", True, "distinct values")

for _k in _shadowed:
    record(f"{_k} (.env vs OS env)", False,
           "OS env overrides .env — remove the OS variable")
if not _shadowed:
    record("no OS env shadows .env", True, "file values are what the app sees")

_CRED_RE = __import__("re").compile(
    r"(API_KEY|_TOKEN|_SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY)$")
_declared = set(FILE_ENV) | {
    ln.split("=", 1)[0].strip()
    for ln in open(os.path.join(BACKEND, ".env.example"), encoding="utf-8",
                   errors="replace").read().splitlines()
    if "=" in ln and not ln.strip().startswith("#")
}
_unmanaged = sorted(
    k for k in os.environ
    if _CRED_RE.search(k) and k not in _declared)
for _k in _unmanaged:
    record(f"{_k} (unmanaged)", False,
           "credential-shaped OS env not declared in .env/.env.example")
if not _unmanaged:
    record("no unmanaged OS credentials", True,
           "every credential-shaped env var is declared")

failed = [n for n, ok, _ in results if not ok]
print("\n" + "=" * 64)
if failed:
    print(f"FAILED {len(failed)}/{len(results)}: {', '.join(failed)}")
    print("Do NOT cut PM2 over until these pass.")
    sys.exit(1)
print(f"ALL {len(results)} CREDENTIALS VERIFIED — safe to proceed to cutover")
sys.exit(0)
