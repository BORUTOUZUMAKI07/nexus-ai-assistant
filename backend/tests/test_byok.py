"""BYOK (Bring-Your-Own-Key) resolution tests.

The feature this guards is the gap it closes: a user could store provider keys
(the settings API existed and was tested), and *nothing read them* -- the raw
key never reached a provider call, so adding a key changed nothing about how a
request was served. Storing a key is not the feature; using it is.

Two independent things have to hold for the wiring to be real, and they are
tested separately:

1. the contextvar resolves the right provider's key (and nothing else), and
2. ``LiteLLMService.complete``/``astream`` actually pass it to the router for
   the attempt group whose provider it belongs to.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.core.security import encrypt_api_key
from backend.app.infrastructure.ai.litellm_client import LiteLLMService
from backend.app.infrastructure.resilience.guards import CircuitBreaker, InFlightLimiter
from backend.app.services.byok import (
    bind_byok_keys,
    byok_keys,
    clear_byok_keys,
    get_byok_key,
    load_user_byok_keys,
    reset_byok_keys,
)

from tests.fakes import FakeSession


@pytest.fixture(autouse=True)
def _clean_context():
    """Every test starts and ends with no keys bound in this context."""
    clear_byok_keys()
    yield
    clear_byok_keys()


# ── the contextvar ───────────────────────────────────────────────────────────


def test_get_byok_key_is_case_insensitive_on_the_provider():
    bind_byok_keys({"Groq": "gsk-1"})
    assert get_byok_key("groq") == "gsk-1"
    assert get_byok_key("GROQ") == "gsk-1"


def test_get_byok_key_returns_none_for_an_absent_provider():
    bind_byok_keys({"groq": "gsk-1"})
    assert get_byok_key("openrouter") is None
    assert get_byok_key(None) is None
    assert get_byok_key("") is None


def test_reset_restores_the_previous_binding_and_does_not_leak():
    """A key bound for one run must not survive into the next."""
    token = bind_byok_keys({"groq": "gsk-1"})
    assert byok_keys() == {"groq": "gsk-1"}
    reset_byok_keys(token)
    assert byok_keys() == {}


def test_a_stale_reset_token_is_tolerated():
    """``reset`` with a token from another context raises; dropping the keys is
    the safe direction and must be what happens."""
    token = bind_byok_keys({"groq": "gsk-1"})
    reset_byok_keys(token)
    # A second reset with the same (now consumed) token must not raise.
    reset_byok_keys(token)
    assert byok_keys() == {}


# ── the loader ───────────────────────────────────────────────────────────────


async def test_load_decrypts_every_active_key():
    session = FakeSession()
    from backend.app.domain.user.models import APIKey

    user_id = uuid4()
    session.seed(
        APIKey,
        [
            APIKey(
                user_id=user_id,
                provider="Groq",
                encrypted_key=encrypt_api_key("gsk-user"),
                key_preview="user",
            ),
            APIKey(
                user_id=user_id,
                provider="openrouter",
                encrypted_key=encrypt_api_key("sk-or-user"),
                key_preview="user",
            ),
        ],
    )

    keys = await load_user_byok_keys(session, user_id)
    assert keys["groq"] == "gsk-user"
    assert keys["openrouter"] == "sk-or-user"


async def test_load_ignores_inactive_and_other_users_rows():
    session = FakeSession()
    from backend.app.domain.user.models import APIKey

    me, someone_else = uuid4(), uuid4()
    session.seed(
        APIKey,
        [
            APIKey(
                user_id=me,
                provider="groq",
                encrypted_key=encrypt_api_key("gsk-mine"),
                key_preview="mine",
                is_active=True,
            ),
            APIKey(
                user_id=me,
                provider="openrouter",
                encrypted_key=encrypt_api_key("sk-or-revoked"),
                key_preview="revoked",
                is_active=False,
            ),
            APIKey(
                user_id=someone_else,
                provider="groq",
                encrypted_key=encrypt_api_key("gsk-theirs"),
                key_preview="them",
                is_active=True,
            ),
        ],
    )

    keys = await load_user_byok_keys(session, me)
    assert keys == {"groq": "gsk-mine"}


async def test_load_skips_a_row_that_will_not_decrypt():
    """A rotated encryption key must not take out the user's other providers."""
    session = FakeSession()
    from backend.app.domain.user.models import APIKey

    user_id = uuid4()
    session.seed(
        APIKey,
        [
            APIKey(
                user_id=user_id,
                provider="groq",
                encrypted_key="not-valid-ciphertext",
                key_preview="bad",
                is_active=True,
            ),
            APIKey(
                user_id=user_id,
                provider="gemini",
                encrypted_key=encrypt_api_key("gemini-user"),
                key_preview="ok",
                is_active=True,
            ),
        ],
    )

    keys = await load_user_byok_keys(session, user_id)
    assert keys == {"gemini": "gemini-user"}


async def test_load_returns_empty_when_the_session_fails():
    """A BYOK lookup that cannot run must degrade to "no key", never raise."""

    class _ExplodingSession:
        async def exec(self, *a, **k):
            raise RuntimeError("no database")

    assert await load_user_byok_keys(_ExplodingSession(), uuid4()) == {}


async def test_load_with_no_user_is_empty():
    assert await load_user_byok_keys(FakeSession(), None) == {}


# ── the router actually receives the key ─────────────────────────────────────


class _Msg:
    def __init__(self, content: str | None):
        self.content = content
        self.tool_calls = None


class _Resp:
    def __init__(self, content: str = "ok"):
        self.choices = [SimpleNamespace(message=_Msg(content))]
        self.usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
        self.model = "qwen3.8-27b"


class _Chunk:
    def __init__(self, content: str | None):
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


class _FakeStream:
    def __init__(self, chunks: list[_Chunk]):
        self._chunks = chunks
        self.model = "qwen3.8-27b"
        self.closed = False

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration as exc:  # noqa: B904 - exact async stop protocol
            raise StopAsyncIteration from exc

    async def aclose(self):
        self.closed = True


class _CapturingRouter:
    """Records the kwargs of every ``acompletion`` call."""

    def __init__(self, chunks: list[_Chunk] | None = None):
        self.calls: list[dict] = []
        self._chunks = chunks or [_Chunk("hi")]

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return _FakeStream(self._chunks)
        return _Resp()


def _service(router):
    # Fresh breaker/limiter per test so success/failure accounting cannot bleed
    # between the module-level singletons of other tests.
    return LiteLLMService(
        llm_router=router,
        circuit_breaker=CircuitBreaker(failure_threshold=99, open_timeout_seconds=1.0),
        in_flight_limiter=InFlightLimiter(max_in_flight=4),
    )


async def test_complete_sends_the_users_key_for_that_provider():
    router = _CapturingRouter()
    service = _service(router)
    # The default group (`complex_reasoning`) resolves to the `groq` provider.
    bind_byok_keys({"groq": "gsk-user-secret"})

    await service.complete([{"role": "user", "content": "hi"}])

    assert router.calls[-1]["api_key"] == "gsk-user-secret"


async def test_complete_without_a_user_key_does_not_send_one():
    """The control: absent BYOK means the deployment key is used, i.e. no
    per-call ``api_key`` override reaches the router."""
    router = _CapturingRouter()
    service = _service(router)

    await service.complete([{"role": "user", "content": "hi"}])

    assert "api_key" not in router.calls[-1]


async def test_astream_sends_the_users_key_for_that_provider():
    router = _CapturingRouter()
    service = _service(router)
    bind_byok_keys({"groq": "gsk-user-secret"})

    chunks = [c async for c in service.astream([{"role": "user", "content": "hi"}])]

    assert chunks == ["hi"]
    assert router.calls[-1]["api_key"] == "gsk-user-secret"
    assert router.calls[-1]["stream"] is True
