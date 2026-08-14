"""
Consent gate for Klaviyo-originated WhatsApp marketing sends.

Phone number alone is NEVER sufficient.

This module is deliberately independent of the B2B lead consent_status used by
whatsapp_sender.py. Klaviyo e-commerce uses native WhatsApp marketing consent
(subscriptions.whatsapp.marketing.consent).

Extensible: when AiSensy's own contact-level opt-in model is verified, a second
check can be added without changing the public interface.
"""
from __future__ import annotations

from dataclasses import dataclass


# Values that authorize a marketing WhatsApp send.
# Aligned with Klaviyo's native consent vocabulary.
MARKETING_CONSENT_OK = {"SUBSCRIBED"}


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str
    source: str = "klaviyo_native_whatsapp_marketing"


def check_whatsapp_marketing_consent(
    whatsapp_marketing_consent: str | None,
    *,
    require_explicit: bool = True,
) -> ConsentDecision:
    """
    Decide whether a marketing WhatsApp may be sent.

    Args:
        whatsapp_marketing_consent: Value from Klaviyo native field
            (subscriptions.whatsapp.marketing.consent) or equivalent.
            Expected: SUBSCRIBED | UNSUBSCRIBED | NEVER_SUBSCRIBED | None.
        require_explicit: If True (default), only SUBSCRIBED is accepted.

    Returns:
        ConsentDecision. Never returns allowed=True for missing/unknown consent.
    """
    raw = (whatsapp_marketing_consent or "").strip().upper()

    if not raw:
        return ConsentDecision(
            allowed=False,
            reason="no WhatsApp marketing consent supplied — phone alone is not consent",
        )

    if raw in MARKETING_CONSENT_OK:
        return ConsentDecision(
            allowed=True,
            reason=f"Klaviyo native WhatsApp marketing consent: {raw}",
        )

    if raw in {"UNSUBSCRIBED", "NEVER_SUBSCRIBED", "REVOKED", "OPTED_OUT"}:
        return ConsentDecision(
            allowed=False,
            reason=f"WhatsApp marketing consent is {raw} — send blocked",
        )

    # Unknown vocabulary — fail closed.
    return ConsentDecision(
        allowed=False,
        reason=(
            f"unrecognised WhatsApp marketing consent value '{raw}' — "
            f"failing closed; only SUBSCRIBED authorises a marketing send"
        ),
    )
