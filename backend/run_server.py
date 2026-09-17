"""
Uvicorn launcher for Windows.

Why this exists instead of `python -m uvicorn app.main:app`:

On Windows, Python 3.8+ defaults asyncio to the ProactorEventLoop. Uvicorn's
`--loop asyncio` therefore gets Proactor, which has a long-standing bug: when a
client disconnects abruptly the accept coroutine can raise
`OSError [WinError 64] The specified network name is no longer available` and
die with "Accept failed on a socket". The process stays alive and the port stays
in LISTENING state, but the server never accepts another connection — so the
service looks healthy to pm2 while every request times out.

Forcing the SelectorEventLoop avoids that failure mode entirely.
"""
import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.observability import setup_logging
    setup_logging("api")

    # Import the application first so its route modules are loaded, then
    # install the last-mile guards before uvicorn can accept traffic.
    import app.main as application
    from app.services.email_send_guard import install_email_send_guard
    from app.services.settings_guard import install_settings_guard
    from app.services.shopify_security import install_shopify_hmac_guard
    install_email_send_guard()
    install_settings_guard(application.app)
    install_shopify_hmac_guard()

    uvicorn.run(
        application.app,
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8003")),
        loop="asyncio",          # now backed by SelectorEventLoop on Windows
        timeout_keep_alive=75,
        backlog=128,
        log_level="warning",
    )
