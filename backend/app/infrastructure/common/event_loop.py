"""uvicorn event-loop factory overrides.

uvicorn 0.36+ resolves the event-loop factory in ``Config.get_loop_factory``
*before* importing the FastAPI app, and on Windows it unconditionally picks
``asyncio.ProactorEventLoop`` — which psycopg 3 cannot use for async I/O.

Point uvicorn at this factory via ``--loop backend.app.infrastructure.common.event_loop:event_loop_factory``
so the durable ``AsyncPostgresSaver`` checkpointer works on Windows dev machines.
"""

from __future__ import annotations

import asyncio
import selectors
import sys


def event_loop_factory() -> asyncio.AbstractEventLoop:
    """Return a Windows-compatible selector event loop.

    Used as uvicorn's ``loop_factory`` on win32 to avoid the ProactorEventLoop
    that psycopg's async mode rejects.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.SelectorEventLoop()
