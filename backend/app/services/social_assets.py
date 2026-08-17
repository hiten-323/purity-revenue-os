"""
Social proof assets used in outreach, proposals, and store embeds.

URLs come from env so production can point at a Purity Beans–owned post
without a code change. The Instagram post currently configured was supplied
by the founder; treat third-party content as provisional until replaced.
"""
from __future__ import annotations

import os

DEFAULT_INSTAGRAM_POST = "https://www.instagram.com/p/Db_nFMaAieK/"


def instagram_post_url() -> str:
    return (
        (os.getenv("SOCIAL_INSTAGRAM_POST") or "").strip()
        or (os.getenv("DOC_INSTAGRAM") or "").strip()
        or DEFAULT_INSTAGRAM_POST
    )


def instagram_embed_html(permalink: str | None = None) -> str:
    """Official Instagram embed block for Shopify custom HTML / blog pages."""
    url = (permalink or instagram_post_url()).rstrip("/")
    return (
        f'<blockquote class="instagram-media" data-instgrm-permalink="{url}/" '
        f'data-instgrm-version="14" style="background:#FFF;border:0;border-radius:3px;'
        f'margin:1px;max-width:540px;min-width:326px;padding:0;width:99.375%;">'
        f'<a href="{url}/" target="_blank" rel="noopener">View this post on Instagram</a>'
        f"</blockquote>\n"
        f'<script async src="//www.instagram.com/embed.js"></script>\n'
    )


def social_assets() -> dict:
    """JSON-safe map for API / email assemblers."""
    ig = instagram_post_url()
    return {
        "instagram_post": ig,
        "instagram_embed_html": instagram_embed_html(ig),
        "channels": ["instagram", "linkedin", "facebook"],
    }
