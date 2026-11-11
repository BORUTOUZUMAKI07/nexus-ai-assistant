"""The refresh grace window has to be a TTL, not a flag.

``rotate_refresh_token`` used to write ``refresh:race:{jti}`` with a bare
``SET``. ``grace_window_seconds`` was only checked in Python on *read*, so the
cached winner lived indefinitely: a captured token replayed days later was still
answered with a working session, and only a test deleting the key by hand ever
exercised the "late replay" path. These tests pin the things that make the
window real -- the entries expire, and nothing is published at all when the
window is disabled.
"""

import pytest
from backend.app.infrastructure.cache.redis_client import RedisService


class _StubRedis:
    """Records the eval call instead of talking to a server."""

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple] = []

    async def eval(self, script, numkeys, *keys_and_args):
        self.calls.append((script, numkeys, keys_and_args))
        return self.reply


async def _rotate(reply, *, grace=10, ttl=3600):
    stub = _StubRedis(reply)
    service = RedisService(stub)
    result = await service.rotate_refresh_token(
        jti="jti-1",
        ttl_seconds=ttl,
        grace_window_seconds=grace,
        race_response='{"access_token": "a"}',
    )
    return result, stub


async def test_the_used_marker_and_the_race_entry_are_both_given_an_expiry():
    """Both keys must carry a TTL, or replay protection is permanent and the
    grace window never closes."""
    _result, stub = await _rotate([0, ""], grace=10, ttl=3600)

    script, numkeys, args = stub.calls[0]
    assert numkeys == 2
    assert "redis.call('SET', used_key, '1', 'EX', ttl)" in script
    assert "redis.call('SET', race_key, race_json, 'EX', grace)" in script
    # The values the script expires with are the ones the caller chose.
    assert args[-2:] == (10, 3600), args


async def test_the_configured_window_is_what_expires_the_race_entry():
    """A different window must reach the script, since that TTL *is* the window."""
    _result, stub = await _rotate([0, ""], grace=60, ttl=7200)

    _script, _numkeys, args = stub.calls[0]
    assert args[-2:] == (60, 7200)


async def test_a_zero_window_publishes_no_replay_entry():
    """``REFRESH_REUSE_GRACE_SECONDS = 0`` must restore strict single use."""
    result, stub = await _rotate([0, ""], grace=0)

    assert result == {"action": "first_use"}
    script, _numkeys, args = stub.calls[0]
    assert "grace > 0" in script
    # args = (key, key, race_json, grace, ttl): ARGV starts after the two keys.
    assert args[2] == "", "a disabled window must not hand the script tokens to cache"


async def test_a_cached_result_is_ignored_when_the_window_is_disabled():
    """Belt and braces: even a stale entry cannot reopen the window."""
    result, _stub = await _rotate([1, '{"access_token": "a"}'], grace=0)

    assert result["action"] == "theft"


async def test_a_cached_result_is_replayed_inside_the_window():
    cached = (
        '{"access_token": "a", "refresh_token": "r",'
        ' "expires_in": 1, "refresh_expires_in": 2}'
    )
    result, _stub = await _rotate([1, cached], grace=10)

    assert result == {"action": "race_replay", "data": cached}


async def test_a_used_jti_with_no_cached_result_is_theft():
    result, _stub = await _rotate([1, ""], grace=10)

    assert result == {"action": "theft"}


@pytest.mark.parametrize("ttl", [0, -5])
async def test_a_nonpositive_ttl_never_writes_a_key_with_no_expiry(ttl):
    """``EX 0`` is an error in Redis; the marker must still expire."""
    _result, stub = await _rotate([0, ""], ttl=ttl)

    _script, _numkeys, args = stub.calls[0]
    assert args[-1] == 1
