"""The device label in the session inventory must come from the browser.

The proxy forwards the browser's User-Agent to FastAPI; these are the three
endpoints that mint a session. Without the wiring below every row in the
inventory would read "Unknown device", which makes the revoke UI a guess.
"""

from types import SimpleNamespace

import pytest
from backend.app.api.v1 import auth as auth_routes
from backend.app.domain.user.schemas import (
    OAuthCallbackRequest,
    TokenResponse,
    TwoFactorVerifyRequest,
)
from backend.app.services.auth.oauth import OAuthIdentity
from fastapi import Response
from fastapi.security import OAuth2PasswordRequestForm

USER_AGENT = "Mozilla/5.0 (Test Device)"


def _tokens() -> TokenResponse:
    return TokenResponse(access_token="access", refresh_token="refresh", expires_in=3600)


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={"user-agent": USER_AGENT},
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.fixture(autouse=True)
def _skip_rate_limit(monkeypatch):
    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth_routes, "_enforce_auth_rate_limit", allow)


@pytest.mark.asyncio
async def test_login_passes_the_browser_user_agent_to_the_service():
    seen: list[str] = []

    class StubAuth:
        async def login(self, identifier, password, user_agent=""):
            seen.append(user_agent)
            return _tokens()

    form = OAuth2PasswordRequestForm(username="user@example.com", password="pw")
    await auth_routes.login(_request(), Response(), form, StubAuth())

    assert seen == [USER_AGENT]


@pytest.mark.asyncio
async def test_two_factor_verify_passes_the_browser_user_agent_to_the_service():
    seen: list[str] = []

    class StubAuth:
        async def verify_2fa(self, preauth_token, code, user_agent=""):
            seen.append(user_agent)
            return _tokens()

    body = TwoFactorVerifyRequest(preauth_token="preauth", code="123456")
    await auth_routes.two_factor_verify(_request(), Response(), body, StubAuth())

    assert seen == [USER_AGENT]


@pytest.mark.asyncio
async def test_oauth_callback_passes_the_browser_user_agent_to_the_service():
    seen: list[str] = []

    class StubAuth:
        async def oauth_login(self, identity, user_agent=""):
            seen.append(user_agent)
            return _tokens()

    class StubOAuth:
        enabled = True

        async def complete_login(self, code, state):
            return OAuthIdentity(provider="google", subject="subject-1", email="user@example.com")

    body = OAuthCallbackRequest(code="code", state="state")
    await auth_routes.oauth_callback(_request(), "google", body, StubAuth(), StubOAuth())

    assert seen == [USER_AGENT]


@pytest.mark.asyncio
async def test_browser_oauth_callback_passes_the_browser_user_agent():
    seen: list[str] = []

    class StubAuth:
        async def oauth_login(self, identity, user_agent=""):
            seen.append(user_agent)
            return _tokens()

    class StubOAuth:
        enabled = True

        async def complete_login(self, code, state):
            return OAuthIdentity(provider="github", subject="subject-2", email="user@example.com")

    await auth_routes.oauth_callback_browser(
        _request(),
        "github",
        code="code",
        state="state",
        auth_svc=StubAuth(),
        oauth_svc=StubOAuth(),
    )

    assert seen == [USER_AGENT]
