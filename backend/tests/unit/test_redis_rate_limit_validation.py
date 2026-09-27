from unittest.mock import AsyncMock

import pytest

from backend.app.infrastructure.cache.redis_client import RedisService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"identifier": "", "limit": 10, "window_seconds": 60, "cost": 1},
        {"identifier": "x" * 257, "limit": 10, "window_seconds": 60, "cost": 1},
        {"identifier": "user", "limit": 0, "window_seconds": 60, "cost": 1},
        {"identifier": "user", "limit": True, "window_seconds": 60, "cost": 1},
        {"identifier": "user", "limit": 10, "window_seconds": 0, "cost": 1},
        {"identifier": "user", "limit": 10, "window_seconds": 60, "cost": 0},
        {"identifier": "user", "limit": 10, "window_seconds": 60, "cost": 11},
    ],
)
async def test_rate_limiter_rejects_invalid_parameters(kwargs):
    client = AsyncMock()
    service = RedisService(client)

    with pytest.raises(ValueError):
        await service.check_rate_limit(**kwargs)

    client.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiter_accepts_valid_parameters():
    client = AsyncMock()
    client.eval.return_value = [1, 9]
    service = RedisService(client)

    allowed, remaining = await service.check_rate_limit(
        identifier="user:123",
        limit=10,
        window_seconds=60,
        cost=1,
    )

    assert allowed is True
    assert remaining == 9
    client.eval.assert_awaited_once()
