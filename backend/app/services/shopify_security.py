"""Shopify webhook authentication shared by the HTTP boundary and tests."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os


def verify_shopify_hmac(body: bytes, signature: str) -> bool:
    secret = (os.getenv("SHOPIFY_WEBHOOK_SECRET", "") or "").strip()
    provided = (signature or "").strip()
    if not secret or not provided:
        return False
    digest = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, provided)


def install_shopify_hmac_guard() -> None:
    # endpoints.py owns the route for compatibility, but its verifier is a
    # module-level function. Replace only that verifier so every existing route
    # and caller gets the corrected base64 Shopify signature semantics.
    from app.api import endpoints
    endpoints._verify_shopify_hmac = verify_shopify_hmac
