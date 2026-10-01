"""`DELETE /admin/users/{user_id}` — the only way to remove someone else's account.

Why this endpoint exists: erasure otherwise happens only through
`DELETE /account`, which requires the *owner's* token. So a probe account, a
user who asked to be removed by email, or a half-finished signup can only be
cleared by raw SQL against production — and raw SQL skips the dependency
ordering in `AccountService.delete_account` and leaves orphans 35 tables deep.

The tests below are mostly about the two refusals, because those are the parts
that cannot be undone by re-reading the user row afterwards.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from backend.app.api.v1 import admin as admin_module
from backend.app.domain.user.models import User
from fastapi import HTTPException


def _await(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeScalarResult:
    """What `session.exec(select(func.count())...)` hands back."""

    def __init__(self, value: int) -> None:
        self._value = value

    async def one(self) -> int:
        return self._value

    def all(self) -> list[int]:  # pragma: no cover - never called
        return [self._value]


class FakeSession:
    """Minimal session: get(User), the admin count, add(), commit().

    Records every statement handed to `exec` as well as every commit, so a test
    can assert on the *shape of the query* rather than on the answer a fake
    chose to return. `exec` is async because SQLModel's `AsyncSession.exec` is
    awaited and then `.one()` is awaited on the result.
    """

    def __init__(self, users: dict[UUID, User], admins_left: int = 1) -> None:
        self._users = users
        self._admins_left = admins_left
        self.added: list[Any] = []
        self.statements: list[Any] = []
        self.commits = 0
        self.commit_raises = False

    async def get(self, model, pk):
        assert model is User, f"unexpected model {model}"
        return self._users.get(pk)

    async def exec(self, stmt):
        self.statements.append(stmt)
        return _FakeExec(self._admins_left)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        if self.commit_raises:
            raise RuntimeError("audit table unavailable")


class _FakeExec:
    def __init__(self, value: int) -> None:
        self._value = value

    async def one(self) -> int:
        return self._value

    # `exec()` is awaited in the route for the count, and used as
    # `select(func.count())...` only; `.first()`/`.all()` would indicate the
    # route started querying rows instead of counting.
    async def first(self):  # pragma: no cover - guards against a wrong rewrite
        raise AssertionError("expected a scalar count, got a row lookup")


class RecordingAccountService:
    """Stands in for the cascade so we can prove the route delegates to it."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.deleted: list[UUID] = []
        self._raises = raises

    async def delete_account(self, user_id: UUID) -> None:
        if self._raises is not None:
            raise self._raises
        self.deleted.append(user_id)


def _user(role: str = "user", is_active: bool = True, email: str = "x@example.com") -> User:
    return User(
        id=uuid4(),
        email=email,
        username=email,
        hashed_password="h",
        role=role,
        is_active=is_active,
    )


def _call(target: UUID, admin: User, session: FakeSession, svc) -> dict:
    return _await(
        admin_module.delete_user(target, session=session, admin=admin, account_svc=svc)
    )


# ── the happy path ────────────────────────────────────────────────────────────


def test_admin_deletes_another_user_through_the_cascade():
    admin = _user(role="admin")
    target = _user(email="probe@example.com")
    session = FakeSession({target.id: target})
    svc = RecordingAccountService()

    result = _call(target.id, admin, session, svc)

    assert svc.deleted == [target.id], (
        "the route must delegate to AccountService.delete_account, not issue "
        "its own deletes -- the dependency ordering across 35 tables lives "
        "there and nowhere else"
    )
    assert result == {"status": "deleted", "user_id": str(target.id)}


def test_deletion_is_audited():
    admin = _user(role="admin")
    target = _user(email="probe@example.com")
    session = FakeSession({target.id: target})

    _call(target.id, admin, session, RecordingAccountService())

    logs = [a for a in session.added if isinstance(a, admin_module.AuditLog)]
    assert len(logs) == 1, f"expected one audit row, got {session.added}"
    entry = logs[0]
    assert entry.action == "admin_user_erasure"
    assert entry.resource_id == str(target.id)
    # The email is the whole reason an admin deletes an account by hand, so a
    # trail that omits it cannot answer "who was that?" later.
    assert entry.details["target_email"] == target.email
    assert entry.user_id == admin.id, "the audit must name the admin, not the victim"


def test_audit_failure_does_not_fail_the_erasure():
    # Erasure already succeeded in the DB by this point; raising here would
    # report a failure for something that did not fail, and the caller would
    # reasonably retry a delete that already happened.
    admin = _user(role="admin")
    target = _user()
    session = FakeSession({target.id: target})
    session.commit_raises = True
    svc = RecordingAccountService()

    result = _call(target.id, admin, session, svc)

    assert svc.deleted == [target.id]
    assert result["status"] == "deleted"


# ── refusals ──────────────────────────────────────────────────────────────────


def test_refuses_to_delete_your_own_account():
    admin = _user(role="admin")
    session = FakeSession({admin.id: admin})
    svc = RecordingAccountService()

    with pytest.raises(HTTPException) as exc:
        _call(admin.id, admin, session, svc)

    assert exc.value.status_code == 400
    assert "your own" in exc.value.detail
    assert svc.deleted == [], "the refusal must happen before the cascade"


def test_refuses_when_the_target_is_the_last_active_admin():
    # This is the case the count exists for. `admins_left == 0` means the only
    # remaining admin would be the one being deleted.
    admin = _user(role="admin")
    target = _user(role="admin", email="other-admin@example.com")
    session = FakeSession({target.id: target}, admins_left=0)
    svc = RecordingAccountService()

    with pytest.raises(HTTPException) as exc:
        _call(target.id, admin, session, svc)

    assert exc.value.status_code == 400
    assert "last active admin" in exc.value.detail
    assert svc.deleted == []


def test_allows_deleting_an_admin_when_another_one_remains():
    admin = _user(role="admin")
    target = _user(role="admin", email="other-admin@example.com")
    session = FakeSession({target.id: target}, admins_left=1)
    svc = RecordingAccountService()

    result = _call(target.id, admin, session, svc)

    assert svc.deleted == [target.id]
    assert result["status"] == "deleted"


def test_the_admin_count_excludes_the_target_and_inactive_admins():
    """The two clauses that make the last-admin guard mean anything.

    Both are load-bearing independently, and asserting the *count* a fake
    returns proves nothing about them -- the fake is the thing under suspicion.

    Without `User.id != user_id` the count includes the row being deleted, so it
    is never zero, the guard never fires, and the endpoint deletes the last
    admin; `/admin` is then unreachable forever with no account left to grant
    the role back.

    Without `is_active.is_(True)` a disabled admin satisfies the count, so the
    last *usable* admin can be deleted while the guard sees a survivor who
    cannot log in.
    """
    admin = _user(role="admin")
    target = _user(role="admin", email="other-admin@example.com")
    session = FakeSession({target.id: target}, admins_left=0)

    with pytest.raises(HTTPException):
        _call(target.id, admin, session, RecordingAccountService())

    assert session.statements, "the guard never queried for other admins"
    sql = str(session.statements[0].compile())
    assert "id != :" in sql, (
        f"the admin count does not exclude the user being deleted: {sql}"
    )
    assert "is_active IS true" in sql, (
        f"the admin count includes disabled admins: {sql}"
    )
    assert "role" in sql, f"the admin count is not filtered by role: {sql}"


def test_unknown_user_is_404_not_500():
    admin = _user(role="admin")
    session = FakeSession({})
    svc = RecordingAccountService()

    with pytest.raises(HTTPException) as exc:
        _call(uuid4(), admin, session, svc)

    assert exc.value.status_code == 404
    assert svc.deleted == []


def test_the_endpoint_is_admin_only():
    # get_current_admin is the gate; asserting the dependency is present is the
    # cheapest way to catch someone dropping it while tidying the signature.
    route = next(
        r
        for r in admin_module.router.routes
        if getattr(r, "path", "") == "/admin/users/{user_id}"
        and "DELETE" in (getattr(r, "methods", None) or set())
    )
    dependant = route.dependant
    names = {d.call.__name__ for d in dependant.dependencies}
    assert "get_current_admin" in names, (
        f"DELETE /admin/users/{{user_id}} is not gated on get_current_admin; "
        f"dependencies are {sorted(names)}"
    )


def test_the_route_is_registered_on_the_v1_router():
    # Not `api_router.routes`: this FastAPI version keeps `include_router`
    # results as `_IncludedRouter` objects rather than flattening them, so the
    # attribute that used to list every path now holds 18 entries with no
    # `.path` at all. `app.openapi()` is the introspection surface that
    # survives either shape, which is also what AGENTS.md says to use.
    from backend.app.main import app

    spec = app.openapi()
    assert "/api/v1/admin/users/{user_id}" in spec["paths"], (
        "admin router is mounted, but DELETE /admin/users/{user_id} is missing "
        "from the OpenAPI document"
    )
    assert "delete" in spec["paths"]["/api/v1/admin/users/{user_id}"], (
        "the path exists but only for other methods; the DELETE verb is the "
        "whole point of this endpoint"
    )
