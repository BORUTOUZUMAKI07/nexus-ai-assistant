"""Run the real Nexus FastAPI app on a live uvicorn socket in-process.

This spawns a dedicated background thread running its own asyncio loop so the
test session (pytest-asyncio) can keep using its own event loops.  The app
serves a dedicated ``nexus_e2e`` database inside a real PostgreSQL
testcontainer (see ``_testcontainers``), wired via
``app.dependency_overrides[deps.get_db]``; every external integration (Redis,
Qdrant, LLM providers, FastMCP, storage) is stubbed by the e2e conftest or left
offline-and-degraded, exactly like the unit/integration tiers.
"""
import asyncio
import socket
import threading
from dataclasses import dataclass

import uvicorn
from _testcontainers import (  # noqa: E402
    build_session_factory,
    build_test_engine,
    init_db_schema,
)
from backend.app.api import deps
from backend.app.main import app


def _pick_free_port() -> int:
    """Bind an ephemeral port, release it, and hand it to uvicorn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ReadyServer(uvicorn.Server):
    """``uvicorn.Server`` that signals once the socket is listening."""

    def __init__(self, config, ready: threading.Event, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self._ready = ready

    async def startup(self, sockets=None):
        await super().startup(sockets)
        self._ready.set()


@dataclass
class LiveServerHandle:
    base_url: str
    _thread: threading.Thread
    _server: _ReadyServer
    _stop_event: threading.Event
    _disposed: list

    def stop(self) -> None:
        if self._disposed:
            return
        self._disposed.append(True)
        self._server.should_exit = True
        self._stop_event.set()
        self._thread.join(timeout=30)


def start_live_server(database_url: str) -> LiveServerHandle:
    """Boot the app on a live socket in a background thread; block until up."""
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"

    ready = threading.Event()
    stop_evt = threading.Event()
    holder: dict = {"server": None}

    def _runner():
        async def _boot():
            engine = build_test_engine(database_url)
            await init_db_schema(engine)
            session_factory = build_session_factory(engine)

            async def _get_db():
                async with session_factory() as session:
                    yield session

            app.dependency_overrides[deps.get_db] = _get_db

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="on",
                log_level="warning",
                access_log=False,
            )
            server = _ReadyServer(config, ready)
            holder["server"] = server
            try:
                await server.serve()
            finally:
                await engine.dispose()
                app.dependency_overrides.pop(deps.get_db, None)

        asyncio.run(_boot())

    thread = threading.Thread(target=_runner, name="e2e-live-server", daemon=True)
    thread.start()

    if not ready.wait(timeout=60):
        raise RuntimeError("e2e server failed to start within 60s")

    server = holder["server"]
    return LiveServerHandle(
        base_url=base_url,
        _thread=thread,
        _server=server,
        _stop_event=stop_evt,
        _disposed=[],
    )
