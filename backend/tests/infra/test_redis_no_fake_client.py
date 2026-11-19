"""The cache client must never silently degrade to an in-process fake.

`redis_client.py` used to install `_InMemoryRedis` at import time whenever
`redis-py` was missing. In production that removed rate limiting and caching
without a single error -- the fake ignored TTLs and raised on Lua scripts.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_production_redis_client_is_a_real_redis_py_client():
    from backend.app.infrastructure.cache import redis_client as module

    assert type(module.redis_client).__module__.startswith("redis.")
    assert type(module.redis_client).__name__ == "Redis"


def test_missing_redis_py_fails_import_instead_of_installing_a_fake():
    """Simulate an environment without redis-py in a fresh interpreter."""
    code = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class BlockRedis(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "redis" or fullname.startswith("redis."):
                    raise ModuleNotFoundError(f"blocked {fullname}")
                return None

        sys.meta_path.insert(0, BlockRedis())
        import backend.app.infrastructure.cache.redis_client  # noqa: F401
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0, "import must fail loudly when redis-py is missing"
    assert "ModuleNotFoundError" in result.stderr
