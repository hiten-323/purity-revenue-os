"""
Product-to-Customer-Fit Recommendation Engine for Purity Revenue OS V2.0.
Authoritative source for Purity Beans product catalogue, pricing, and fit logic.

Strict rules:
1. Live Shopify product data truth (prices, variants, SKUs, inventory).
2. Never invent prices, discounts, margins, MOQ, or delivery commitments.
3. Zero chicory in Purica, Purista, Bold, Gourmet Duo.
4. Ultra Blend contains chicory (Robusta + Chicory blend, agglomerated).
5. Variety Box contains Ultra Blend, hence contains chicory (full disclosure mandatory).
6. Prima is currently out of stock on Shopify (inventory: 0); recommend Purica or Bold instead.
7. Never recommend products solely based on business category. Evaluate taste, strength,
   format, price sensitivity, and chicory preference.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

_log = logging.getLogger("product_fit_engine")

# Authoritative Shopify Product Catalogue
PRODUCT_CATALOGUE: Dict[str, Dict[str, Any]] = {
    "PURICA": {
        "family": "Purica",
        "description": "Freeze-Dried Full Arabica instant coffee granules. 100% pure coffee, zero chicory.",
        "roast_profile": "Medium roast, caramel and chocolate notes, smooth finish, low acidity.",
        "coffee_type": "Arabica",
        "format": "Freeze-Dried Granules",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": True,
        "variants": {
            "50g": {"sku": "PB-PCA-50", "mrp": 299, "price": 299, "weight_g": 50, "container": "Glass Jar"},
            "100g": {"sku": "PB-PCA-100", "mrp": 559, "price": 559, "weight_g": 100, "container": "Glass Jar"}
        },
        "url": "https://www.p3online.in/products/purica-freeze-dried-arabica"
    },
    "PURISTA": {
        "family": "Purista",
        "description": "Freeze-Dried Full Robusta instant coffee granules. 100% pure coffee, zero chicory.",
        "roast_profile": "Dark roast, bold crema, intense aroma, high caffeine kick.",
        "coffee_type": "Robusta",
        "format": "Freeze-Dried Granules",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": True,
        "variants": {
            "50g": {"sku": "PB-PST-50", "mrp": 289, "price": 289, "weight_g": 50, "container": "Glass Jar"},
            "100g": {"sku": "PB-PST-100", "mrp": 459, "price": 459, "weight_g": 100, "container": "Glass Jar"}
        },
        "url": "https://www.p3online.in/products/purista-freeze-dried-robusta"
    },
    "BOLD": {
        "family": "Bold",
        "description": "Agglomerated instant coffee. 100% pure coffee, zero chicory.",
        "roast_profile": "Strong punch, dark roast, rich cup, economical pure coffee.",
        "coffee_type": "Robusta Blend",
        "format": "Agglomerated Granules",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": True,
        "variants": {
            "50g": {"sku": "PB-BLD-50", "mrp": 215, "price": 215, "weight_g": 50, "container": "Glass Jar"},
            "100g": {"sku": "PB-BLD-100", "mrp": 329, "price": 329, "weight_g": 100, "container": "Glass Jar"}
        },
        "url": "https://www.p3online.in/products/bold-agglomerated-instant-coffee"
    },
    "ULTRA_BLEND": {
        "family": "Ultra Blend",
        "description": "Robusta instant coffee blended with roasted chicory. Agglomerated granules.",
        "roast_profile": "Traditional South Indian filter coffee style profile, heavy body with milk, cost-effective.",
        "coffee_type": "Coffee + Chicory Blend",
        "format": "Agglomerated Granules",
        "has_chicory": True,
        "is_pure_coffee": False,
        "in_stock": True,
        "variants": {
            "50g": {"sku": "PB-ULT-50", "mrp": 209, "price": 209, "weight_g": 50, "container": "Glass Jar"},
            "100g": {"sku": "PB-ULT-100", "mrp": 309, "price": 309, "weight_g": 100, "container": "Glass Jar"}
        },
        "url": "https://www.p3online.in/products/ultra-blend-coffee-chicory"
    },
    "PRIMA": {
        "family": "Prima",
        "description": "Agglomerated pure Arabica coffee.",
        "roast_profile": "Mild, smooth pure Arabica.",
        "coffee_type": "Arabica",
        "format": "Agglomerated Granules",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": False,  # Currently 0 inventory on Shopify
        "variants": {
            "50g": {"sku": "PB-PRM-50", "mrp": 269, "price": 269, "weight_g": 50, "container": "Glass Jar"},
            "100g": {"sku": "PB-PRM-100", "mrp": 449, "price": 449, "weight_g": 100, "container": "Glass Jar"}
        },
        "url": "https://www.p3online.in/products/prima-arabica"
    },
    "GOURMET_DUO_MINI": {
        "family": "Gourmet Duo Mini",
        "description": "Tasting Pack: Purista 50g + Purica 50g. 100% pure coffee, zero chicory. Free shipping.",
        "roast_profile": "Complete pure coffee experience (Premium Arabica + Gourmet Robusta).",
        "coffee_type": "Arabica & Robusta Combo",
        "format": "Freeze-Dried Granules (2 x 50g Jars)",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": True,
        "variants": {
            "combo_2x50g": {"sku": "PB-GOURMET-DUO-50", "mrp": 588, "price": 570, "weight_g": 100, "container": "Box of 2 Jars"}
        },
        "url": "https://www.p3online.in/products/gourmet-duo-mini"
    },
    "VARIETY_BOX": {
        "family": "Variety Box",
        "description": "4-Jar Starter Box: Purica 50g, Purista 50g, Bold 50g, Ultra Blend 50g. Free shipping. (Ultra Blend contains chicory).",
        "roast_profile": "Full spectrum: Freeze-dried Arabica & Robusta, Agglomerated pure coffee & Chicory blend.",
        "coffee_type": "Variety Pack (Pure Coffee & Chicory Blend)",
        "format": "Mixed (2 Freeze-Dried, 2 Agglomerated Jars)",
        "has_chicory": True,  # Contains Ultra Blend
        "is_pure_coffee": False,
        "in_stock": True,
        "variants": {
            "combo_4x50g": {"sku": "PB-VARIETY-4x50", "mrp": 1012, "price": 877, "weight_g": 200, "container": "Box of 4 Jars"}
        },
        "url": "https://www.p3online.in/products/variety-box"
    },
    "GOURMET_DUO_FULL": {
        "family": "Gourmet Duo Full",
        "description": "Full Pack: Purista 100g + Purica 100g. 100% pure coffee, zero chicory. Free shipping.",
        "roast_profile": "Complete pure coffee experience (100g Arabica + 100g Robusta).",
        "coffee_type": "Arabica & Robusta Combo",
        "format": "Freeze-Dried Granules (2 x 100g Jars)",
        "has_chicory": False,
        "is_pure_coffee": True,
        "in_stock": True,
        "variants": {
            "combo_2x100g": {"sku": "PB-GOURMET-DUO-100", "mrp": 1018, "price": 908, "weight_g": 200, "container": "Box of 2 Jars"}
        },
        "url": "https://www.p3online.in/products/gourmet-duo-100"
    }
}


@dataclass
class ProductRecommendation:
    primary_sku: str
    primary_product: str
    price: float
    mrp: float
    format: str
    roast_notes: str
    rationale: str
    starter_pack_sku: str
    starter_pack_name: str
    starter_pack_price: float
    checkout_url: str
    blend_disclosure: str
    wholesale_eligible: bool = True
    competitor_displacement_note: str = ""


class ProductFitEngine:
    """
    Intelligent recommendation engine matching prospect preference, strength requirement,
    price sensitivity, and format to authoritative Purity Beans SKUs.
    """

    @classmethod
    def recommend(
        cls,
        taste_preference: Optional[str] = None,
        strength_preference: Optional[str] = None,
        chicory_allowed: Optional[bool] = None,
        price_sensitivity: str = "medium",  # "low", "medium", "high"
        business_type: Optional[str] = None,
        current_competitor: Optional[str] = None,
        is_first_order: bool = True
    ) -> ProductRecommendation:
        """
        Determines the optimal product recommendation based on verified criteria.
        """
        taste = (taste_preference or "").lower()
        strength = (strength_preference or "").lower()
        competitor = (current_competitor or "").lower()

        # Rule 1: Zero chicory explicit requirement
        # If client explicitly specifies pure coffee or zero chicory, chicory_allowed is False
        if "pure" in taste or "pure coffee" in strength or "no chicory" in taste or chicory_allowed is False:
            chicory_allowed = False
        elif "chicory" in taste or "filter" in taste or "south indian" in taste:
            chicory_allowed = True

        # Rule 2: Competitor displacement clues
        comp_note = ""
        if "davidoff" in competitor:
            comp_note = "Davidoff displacement: Purica freeze-dried Arabica offers identical rich notes at direct factory pricing."
        elif "nescafe gold" in competitor:
            comp_note = "Nescafe Gold displacement: Purica provides freeze-dried pure Arabica with chocolate notes."
        elif "nescafe classic" in competitor:
            comp_note = "Nescafe Classic displacement: Bold provides 100% pure agglomerated coffee without bitter bite."
        elif "bru" in competitor or "sunrise" in competitor:
            comp_note = "Bru/Sunrise displacement: Ultra Blend matches traditional chicory-rich taste at competitive wholesale rates."

        # Rule 3: Product matching logic
        # A. Explicit Arabica / Mild / Caramel / Chocolate / Specialty
        if "arabica" in taste or "mild" in strength or "smooth" in strength or "caramel" in taste or "specialty" in taste:
            primary_key = "PURICA"
            variant = "100g" if price_sensitivity == "low" else "50g"
            rationale = "Customer prefers smooth, aromatic Arabica with caramel/chocolate notes. Purica Freeze-Dried Arabica is the ideal match."

        # B. Strong / Robusta / Bold Crema / High Caffeine / Espresso style
        elif "robusta" in taste or "strong" in strength or "crema" in strength or "caffeine" in strength or "intense" in strength:
            if chicory_allowed is True and price_sensitivity == "high":
                primary_key = "ULTRA_BLEND"
                variant = "100g"
                rationale = "High-strength economical requirement with chicory acceptance. Ultra Blend provides traditional strong body with milk."
            elif price_sensitivity == "high":
                primary_key = "BOLD"
                variant = "100g"
                rationale = "Customer requires strong pure coffee on a tight budget. Bold agglomerated pure coffee provides maximum strength without chicory."
            else:
                primary_key = "PURISTA"
                variant = "50g" if price_sensitivity == "medium" else "100g"
                rationale = "Customer desires strong, intense coffee with rich crema. Purista Freeze-Dried Robusta delivers full body and aroma."

        # C. Price sensitive / Value / Volume pantry
        elif price_sensitivity == "high":
            if chicory_allowed is True:
                primary_key = "ULTRA_BLEND"
                variant = "100g"
                rationale = "Value-oriented volume procurement. Ultra Blend delivers cost efficiency with rich traditional flavor."
            else:
                primary_key = "BOLD"
                variant = "50g"
                rationale = "Value-oriented pure coffee requirement. Bold delivers 100% pure coffee at an accessible price point."

        # C2. Explicit Chicory / Filter Coffee requirement
        elif chicory_allowed is True or "chicory" in taste or "filter" in taste:
            primary_key = "ULTRA_BLEND"
            variant = "50g" if price_sensitivity == "medium" else "100g"
            rationale = "Customer requested chicory blend or South Indian filter profile. Ultra Blend delivers authentic rich taste."

        # D. Default / Balanced
        else:
            primary_key = "PURISTA"
            variant = "50g"
            rationale = "Balanced all-rounder recommendation: Purista freeze-dried Robusta for bold everyday coffee."

        product_data = PRODUCT_CATALOGUE[primary_key]
        var_info = product_data["variants"][variant]

        # Starter pack recommendation logic:
        # If chicory is explicitly disallowed, recommend Gourmet Duo Mini (₹570).
        # Otherwise, recommend Variety Box (₹877) for 4-flavour testing.
        if chicory_allowed is False:
            starter_key = "GOURMET_DUO_MINI"
            starter_sku = "PB-GOURMET-DUO-50"
            starter_name = "Gourmet Duo Mini (Purista 50g + Purica 50g)"
            starter_price = 570.0
            starter_url = "https://www.p3online.in/products/gourmet-duo-mini"
            disclosure = "100% Pure Coffee, Zero Chicory."
        else:
            starter_key = "VARIETY_BOX"
            starter_sku = "PB-VARIETY-4x50"
            starter_name = "Variety Box (4 x 50g: Purica, Purista, Bold, Ultra Blend)"
            starter_price = 877.0
            starter_url = "https://www.p3online.in/products/variety-box"
            disclosure = "Ultra Blend component contains chicory. Purica, Purista, and Bold are 100% pure coffee."

        return ProductRecommendation(
            primary_sku=var_info["sku"],
            primary_product=f"{product_data['family']} ({variant})",
            price=float(var_info["price"]),
            mrp=float(var_info["mrp"]),
            format=product_data["format"],
            roast_notes=product_data["roast_profile"],
            rationale=rationale,
            starter_pack_sku=starter_sku,
            starter_pack_name=starter_name,
            starter_pack_price=starter_price,
            checkout_url=starter_url if is_first_order else product_data["url"],
            blend_disclosure=disclosure,
            wholesale_eligible=True,
            competitor_displacement_note=comp_note
        )

    @classmethod
    def get_sku_details(cls, sku: str) -> Optional[Dict[str, Any]]:
        """Look up SKU in catalogue."""
        for fam_key, fam_data in PRODUCT_CATALOGUE.items():
            for v_key, v_info in fam_data.get("variants", {}).items():
                if v_info.get("sku") == sku:
                    return {
                        "family": fam_data["family"],
                        "variant": v_key,
                        "sku": sku,
                        "price": v_info["price"],
                        "mrp": v_info["mrp"],
                        "has_chicory": fam_data["has_chicory"],
                        "format": fam_data["format"],
                        "in_stock": fam_data["in_stock"],
                        "url": fam_data["url"]
                    }
        return None
