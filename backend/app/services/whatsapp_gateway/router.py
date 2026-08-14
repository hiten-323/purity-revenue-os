"""
Deterministic campaign router.

Maps lifecycle stages to AiSensy campaign names without scattering string
literals through the codebase. Configurable via env or constructor.

Does NOT activate or send any campaign.
"""
from __future__ import annotations

import os
from typing import Mapping


# Default mapping — values are placeholders until real AiSensy campaign names
# are confirmed. Override with env or constructor.
DEFAULT_CAMPAIGN_MAP: dict[str, str] = {
    "ATC": "PB_ATC_01",
    "CHECKOUT": "PB_CHECKOUT_01",
    "POST_PURCHASE": "PB_POST_PURCHASE_01",
    "POST_PURCHASE_DAY20": "PB_POST_PURCHASE_DAY20",
    "ORDER_CONFIRMATION": "PB_ORDER_CONFIRMATION",
}


def _env_overrides() -> dict[str, str]:
    """
    Optional overrides:
      WA_CAMPAIGN_ATC=...
      WA_CAMPAIGN_CHECKOUT=...
      etc.
    """
    mapping = {}
    for stage in DEFAULT_CAMPAIGN_MAP:
        env_key = f"WA_CAMPAIGN_{stage}"
        val = (os.getenv(env_key) or "").strip()
        if val:
            mapping[stage] = val
    return mapping


class CampaignRouter:
    def __init__(self, mapping: Mapping[str, str] | None = None):
        base = dict(DEFAULT_CAMPAIGN_MAP)
        base.update(_env_overrides())
        if mapping:
            base.update({k.upper(): v for k, v in mapping.items()})
        self._map = base

    def resolve(self, lifecycle_stage: str, explicit_campaign: str | None = None) -> str | None:
        if explicit_campaign and explicit_campaign.strip():
            return explicit_campaign.strip()
        stage = (lifecycle_stage or "").strip().upper()
        return self._map.get(stage)

    def known_stages(self) -> list[str]:
        return sorted(self._map.keys())


def resolve_campaign(
    lifecycle_stage: str,
    explicit_campaign: str | None = None,
    mapping: Mapping[str, str] | None = None,
) -> str | None:
    return CampaignRouter(mapping).resolve(lifecycle_stage, explicit_campaign)
