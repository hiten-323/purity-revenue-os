"""
Marketplace Director — one orchestrator, per-marketplace agents beneath it.

WHY THE SHELL AND NOT THE FIFTEEN SCREENS
The proposed dashboard asks for Today's Sales, Buy Box %, ACoS, Prime status,
keyword ranks, competitor pricing and review sentiment. Every one of those needs
a live marketplace connection, and right now there is none: no SP-API
credentials, the marketplaces table holds 0 rows, inventory holds 0.

Fifteen screens built on that would show zeros, or — the failure this codebase
has already paid for twice — plausible modelled numbers that read as measured.
"Buy Box 87%" on a homepage is a decision-driver, and inventing it is worse than
showing nothing.

So this is the shell and the honest status layer. Each capability declares what
it NEEDS and what is MISSING, so the dashboard can say "Buy Box: needs SP-API
credentials" rather than "Buy Box: 0%". The moment credentials exist the same
structure carries real data, because amazon_connector.py already implements LWA
auth and SigV4 signing — the plumbing is built, only the keys are absent.

COMMON VS MARKETPLACE-SPECIFIC
Sales, orders, inventory, pricing and reviews are common to every marketplace and
belong to the Director. Buy Box, Prime and PPC are Amazon's and appear only
under Amazon, exactly as proposed — a unified interface should not pretend
Blinkit has a Buy Box.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Capabilities every marketplace shares. The Director aggregates these.
COMMON = ("sales", "orders", "inventory", "pricing", "reviews", "listings")


@dataclass
class Marketplace:
    key: str
    label: str
    common: tuple = COMMON
    specific: tuple = ()          # capabilities unique to this marketplace
    env_keys: tuple = ()          # credentials required to fetch anything
    connector: str | None = None  # module that implements the calls

    def missing_credentials(self) -> list[str]:
        return [k for k in self.env_keys if not (os.getenv(k) or "").strip()]

    def connected(self) -> bool:
        return bool(self.env_keys) and not self.missing_credentials()


MARKETPLACES = {
    "amazon": Marketplace(
        "amazon", "Amazon India",
        specific=("buy_box", "prime_eligibility", "ppc", "keyword_rank",
                  "account_health", "a_plus_content"),
        env_keys=("AMAZON_LWA_CLIENT_ID", "AMAZON_LWA_CLIENT_SECRET",
                  "AMAZON_REFRESH_TOKEN", "AMAZON_SELLER_ID"),
        connector="amazon_connector"),
    "flipkart": Marketplace(
        "flipkart", "Flipkart",
        specific=("smart_assort", "fassured"),
        env_keys=("FLIPKART_CLIENT_ID", "FLIPKART_CLIENT_SECRET")),
    "blinkit": Marketplace(
        "blinkit", "Blinkit",
        specific=("dark_store_stock",),
        env_keys=("BLINKIT_API_KEY",)),
    "zepto": Marketplace(
        "zepto", "Zepto",
        specific=("dark_store_stock",),
        env_keys=("ZEPTO_API_KEY",)),
    "jiomart": Marketplace(
        "jiomart", "JioMart", env_keys=("JIOMART_API_KEY",)),
    "ondc": Marketplace(
        "ondc", "ONDC",
        specific=("network_catalog",),
        env_keys=("ONDC_SUBSCRIBER_ID", "ONDC_SIGNING_KEY")),
}

# What each capability would need, so a blank panel can explain itself rather
# than showing a zero the founder has to interpret.
NEEDS = {
    "sales":            "orders API — seller credentials",
    "orders":           "orders API — seller credentials",
    "inventory":        "inventory/FBA API — seller credentials",
    "pricing":          "pricing API — seller credentials",
    "reviews":          "reviews feed or scrape — seller credentials",
    "listings":         "catalogue API — seller credentials",
    "buy_box":          "SP-API Product Pricing (competitive offers)",
    "prime_eligibility": "SP-API FBA/eligibility",
    "ppc":              "Amazon Ads API — a SEPARATE authorisation from SP-API",
    "keyword_rank":     "search-rank source — Ads API or a rank tracker",
    "account_health":   "SP-API account health",
    "a_plus_content":   "SP-API A+ content",
}


def status() -> dict:
    """
    What is connected, what is not, and precisely what each gap needs.

    Never reports a metric it cannot source. A capability with no credentials is
    NOT_CONNECTED with the missing keys named — the founder should be able to
    read this and know exactly what to go and get.
    """
    out = []
    for mk in MARKETPLACES.values():
        missing = mk.missing_credentials()
        caps = []
        for c in tuple(mk.common) + tuple(mk.specific):
            caps.append({
                "capability": c,
                "scope": "common" if c in mk.common else mk.label,
                "state": "AVAILABLE" if mk.connected() else "NOT_CONNECTED",
                "needs": NEEDS.get(c, "marketplace credentials"),
            })
        out.append({
            "marketplace": mk.key,
            "label": mk.label,
            "connected": mk.connected(),
            "missing_credentials": missing,
            "connector_implemented": bool(mk.connector),
            "capabilities": caps,
        })
    connected = [m for m in out if m["connected"]]
    return {
        "marketplaces": out,
        "connected_count": len(connected),
        "total": len(out),
        "headline": (
            f"{len(connected)} of {len(out)} marketplaces connected."
            if connected else
            "No marketplace is connected — every panel would be empty. "
            "Amazon's connector is implemented and waiting on credentials; "
            "the others need both."),
        "note": ("Metrics are never modelled here. A capability without a live "
                 "source reports NOT_CONNECTED and names what it needs, because "
                 "an invented Buy Box percentage on a dashboard drives real "
                 "decisions."),
    }


def amazon_probe() -> dict:
    """
    Try the Amazon connector for real and report exactly what happened.

    Answering "is Amazon connected?" by looking at the environment is a guess;
    this asks Amazon. A failure here is a useful answer, not an error to hide.
    """
    mk = MARKETPLACES["amazon"]
    missing = mk.missing_credentials()
    if missing:
        return {"connected": False, "reason": "missing credentials",
                "missing": missing,
                "next_step": ("Create an SP-API app in Seller Central, then set "
                              "these in backend/.env")}
    try:
        import importlib
        conn = importlib.import_module("amazon_connector")
        tok = getattr(conn, "_get_lwa_token", None)
        if tok is None:
            return {"connected": False, "reason": "connector has no token method"}
        tok()
        return {"connected": True, "reason": "LWA token obtained"}
    except Exception as e:
        return {"connected": False, "reason": f"{type(e).__name__}: {str(e)[:160]}"}
