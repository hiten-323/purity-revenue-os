"""
Uvicorn launcher for Windows.

Forces the SelectorEventLoop on Windows so abrupt client disconnects do not
leave a process listening on the port but unable to accept new requests.
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

    # Apply the idempotent schema delta before uvicorn accepts traffic.
    from app.database.startup_migrations import run_startup_migrations
    run_startup_migrations()

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("API_PORT", "8003")),
        loop="asyncio",
        timeout_keep_alive=75,
        backlog=128,
        log_level="warning",
    )
