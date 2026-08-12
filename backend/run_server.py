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
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(__import__("os").getenv("API_PORT", "8003")),
        loop="asyncio",          # now backed by SelectorEventLoop on Windows
        timeout_keep_alive=75,
        backlog=128,
        log_level="warning",
    )
