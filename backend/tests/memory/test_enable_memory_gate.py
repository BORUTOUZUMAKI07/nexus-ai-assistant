"""The explicit ``enable_memory`` setting must gate automatic memory paths.

The default remains enabled and every lookup is fail-open: a missing settings
row, a bad id or a database outage must keep today's behaviour rather than
silently removing personalisation from existing users.
"""
from uuid import uuid4

import pytest
from backend.app.domain.user.models import UserSettings
from backend.app.services.memory import preferences
from backend.tests.fakes import FakeSession


@pytest.mark.asyncio
async def test_memory_enabled_defaults_true_without_a_settings_row():
    assert await preferences.is_memory_enabled(uuid4(), session=FakeSession()) is True


@pytest.mark.asyncio
async def test_memory_enabled_respects_an_explicit_opt_out():
    user_id = uuid4()
    session = FakeSession()
    session.seed(UserSettings, [UserSettings(user_id=user_id, enable_memory=False)])

    assert await preferences.is_memory_enabled(user_id, session=session) is False


@pytest.mark.asyncio
async def test_memory_enabled_fails_open_when_the_settings_lookup_errors(monkeypatch):
    async def _boom(_self, _user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(preferences.UserRepository, "is_memory_enabled", _boom)

    assert await preferences.is_memory_enabled(uuid4(), session=FakeSession()) is True
