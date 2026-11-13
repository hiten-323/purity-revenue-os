"""Boot-time patches for LIVE purity processes (locks + trust confidence)."""
from __future__ import annotations

def apply() -> None:
    try:
        import app.services.call_db_lock_patch  # noqa: F401
    except Exception as e:
        print(f"[purity_boot_patches] call lock patch failed: {e}")
    try:
        import app.services.trust_confidence  # noqa: F401
    except Exception as e:
        print(f"[purity_boot_patches] trust confidence patch failed: {e}")

apply()
