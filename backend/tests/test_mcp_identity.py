"""P0 #6 — MCP tools must bind identity to the authenticated caller, not to an
argument. Before this, ``create_artifact(user_id=...)`` and
``request_user_input(conversation_id=...)`` trusted whatever the caller passed,
so any authenticated MCP client could act as any user (broken object-level
authorization). These tests pin the binding and the ownership check.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

import backend.app.mcp.server as server


class _Ctx:
    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *exc):
        return False


# ── identity resolution ───────────────────────────────────────────────────────


def test_authenticated_user_id_is_none_without_an_http_request(monkeypatch):
    import fastmcp.server.dependencies as deps

    def _boom():
        raise RuntimeError("no request context")

    monkeypatch.setattr(deps, "get_http_request", _boom)
    assert server._authenticated_user_id() is None


def test_authenticated_user_id_reads_the_bearer_token(monkeypatch):
    import fastmcp.server.dependencies as deps
    import backend.app.core.security as sec

    uid = uuid4()

    class _Req:
        headers = {"Authorization": "Bearer tok"}
        cookies = {}

    monkeypatch.setattr(deps, "get_http_request", lambda: _Req())
    monkeypatch.setattr(sec, "decode_token", lambda _t: {"type": "access", "sub": str(uid)})
    assert server._authenticated_user_id() == uid


def test_authenticated_user_id_rejects_a_non_access_token(monkeypatch):
    import fastmcp.server.dependencies as deps
    import backend.app.core.security as sec

    class _Req:
        headers = {"Authorization": "Bearer tok"}
        cookies = {}

    monkeypatch.setattr(deps, "get_http_request", lambda: _Req())
    monkeypatch.setattr(sec, "decode_token", lambda _t: {"type": "refresh", "sub": str(uuid4())})
    assert server._authenticated_user_id() is None


# ── create_artifact ───────────────────────────────────────────────────────────


class _OwnedRepo:
    def __init__(self, session):
        pass

    async def get_by_id(self, cid, user_id=None):
        return object()


class _NotOwnedRepo:
    def __init__(self, session):
        pass

    async def get_by_id(self, cid, user_id=None):
        return None


class _RecordingArtifacts:
    last = None

    def __init__(self, session):
        pass

    async def create(self, user_id, payload):
        artifact = SimpleNamespace(
            id=uuid4(), title=payload.title, version=1, language=payload.language
        )
        _RecordingArtifacts.last = SimpleNamespace(user_id=user_id, artifact=artifact)
        return artifact


@pytest.mark.asyncio
async def test_create_artifact_binds_owner_to_the_caller(monkeypatch):
    caller, attacker = uuid4(), uuid4()
    monkeypatch.setattr(server, "_authenticated_user_id", lambda: caller)

    import backend.app.services.artifact_service as art_mod
    import backend.app.infrastructure.database.session as sess_mod

    monkeypatch.setattr(art_mod, "ArtifactService", _RecordingArtifacts)
    monkeypatch.setattr(sess_mod, "async_session_factory", lambda: _Ctx(object()))

    res = await server.create_artifact(user_id=str(attacker), title="T", content="hello world")

    assert "Artifact persisted" in res
    assert _RecordingArtifacts.last.user_id == caller
    assert _RecordingArtifacts.last.user_id != attacker


@pytest.mark.asyncio
async def test_create_artifact_refuses_a_conversation_the_caller_does_not_own(monkeypatch):
    monkeypatch.setattr(server, "_authenticated_user_id", lambda: uuid4())

    import backend.app.services.artifact_service as art_mod
    import backend.app.domain.conversation.repository as conv_mod
    import backend.app.infrastructure.database.session as sess_mod

    calls = {"create": 0}

    class _CountingArtifacts(_RecordingArtifacts):
        async def create(self, user_id, payload):
            calls["create"] += 1
            return await super().create(user_id, payload)

    monkeypatch.setattr(art_mod, "ArtifactService", _CountingArtifacts)
    monkeypatch.setattr(conv_mod, "ConversationRepository", _NotOwnedRepo)
    monkeypatch.setattr(sess_mod, "async_session_factory", lambda: _Ctx(object()))

    res = await server.create_artifact(
        user_id=str(uuid4()), title="T", content="hello world", conversation_id=str(uuid4())
    )

    assert "conversation not found" in res
    assert calls["create"] == 0


@pytest.mark.asyncio
async def test_shared_key_path_may_still_pass_an_explicit_user_id(monkeypatch):
    """The trusted service credential has no user identity, so it supplies one."""
    monkeypatch.setattr(server, "_authenticated_user_id", lambda: None)
    explicit = uuid4()

    import backend.app.services.artifact_service as art_mod
    import backend.app.infrastructure.database.session as sess_mod

    monkeypatch.setattr(art_mod, "ArtifactService", _RecordingArtifacts)
    monkeypatch.setattr(sess_mod, "async_session_factory", lambda: _Ctx(object()))

    res = await server.create_artifact(user_id=str(explicit), title="T", content="hi there")
    assert "Artifact persisted" in res
    assert _RecordingArtifacts.last.user_id == explicit


# ── request_user_input ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_user_input_refuses_a_conversation_the_caller_does_not_own(monkeypatch):
    monkeypatch.setattr(server, "_authenticated_user_id", lambda: uuid4())

    import backend.app.services.tools.elicitations as elic_mod
    import backend.app.domain.conversation.repository as conv_mod
    import backend.app.infrastructure.database.session as sess_mod

    parked = {"n": 0}

    class _ElicitationService:
        def __init__(self, session):
            pass

        async def park_elicitation(self, **kwargs):
            parked["n"] += 1
            return {"elicitation_id": "x"}

    monkeypatch.setattr(elic_mod, "ElicitationService", _ElicitationService)
    monkeypatch.setattr(conv_mod, "ConversationRepository", _NotOwnedRepo)
    monkeypatch.setattr(sess_mod, "async_session_factory", lambda: _Ctx(object()))

    res = await server.request_user_input(
        conversation_id=str(uuid4()), message="?", schema_json="{}"
    )

    assert "conversation not found" in res
    assert parked["n"] == 0
