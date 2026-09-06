"""
Pytest configuration, path fixtures, and shared database/client/user fixtures.

The heavy fixtures below are opt-in (lazy): standalone unit tests continue to
run without a database, while the ``tests/integration`` and ``tests/e2e``
tiers request them explicitly.  Every repository and route under test runs
against a real Dockerized PostgreSQL (see ``_testcontainers``) — the same
dialect, JSONB/ARRAY columns, and constraint behavior as production.
"""
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure root directory and backend directory are in sys.path
root_dir = Path(__file__).resolve().parent.parent.parent
backend_dir = Path(__file__).resolve().parent.parent
tests_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(root_dir))
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(tests_dir))

from _testcontainers import (  # noqa: E402
    build_session_factory,
    build_test_engine,
    get_postgres_url,
    init_db_schema,
    stop_containers,
)
from backend.app.api import deps  # noqa: E402
from backend.app.core import security  # noqa: E402
from backend.app.domain.user.repository import UserRepository  # noqa: E402
from backend.app.domain.user.schemas import UserCreate  # noqa: E402
from backend.app.main import app  # noqa: E402

TEST_PASSWORD = "TestPass123!"


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover
    """Stop any testcontainers that were started during the session."""
    stop_containers()


# ─────────────────────────────────────────────────────────────────────────── #
# Database
# ─────────────────────────────────────────────────────────────────────────── #


@pytest.fixture(scope="session")
def db_engine():
    """Session-scoped async engine backed by a real Postgres testcontainer."""
    url = get_postgres_url("nexus_test")
    engine = build_test_engine(url)
    yield engine
    import asyncio

    asyncio.run(engine.dispose())


@pytest.fixture(scope="session")
def session_factory(db_engine):
    """Session-scoped sqlmodel AsyncSession factory bound to ``db_engine``."""
    return build_session_factory(db_engine)


@pytest.fixture(scope="session")
async def schema_ready(db_engine):
    """Drop and recreate all tables once per session (deterministic state)."""
    await init_db_schema(db_engine)


@pytest.fixture
async def db_session(session_factory, schema_ready):
    """Function-scoped AsyncSession for direct repository/service tests."""
    async with session_factory() as session:
        yield session


# ─────────────────────────────────────────────────────────────────────────── #
# FastAPI application + dependency overrides
# ─────────────────────────────────────────────────────────────────────────── #


@pytest.fixture
async def override_get_db(session_factory, schema_ready):
    """Override ``get_db``/``get_db_session`` to serve Postgres-backed sessions.

    Every route and service dependency built on ``Depends(get_db)`` (auth,
    conversation, usage, settings, prompts, tools, files) cascades onto the
    testcontainer factory.
    """

    async def _get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[deps.get_db] = _get_db
    yield
    app.dependency_overrides.pop(deps.get_db, None)


@pytest.fixture
async def client(override_get_db):
    """httpx AsyncClient talking to the ASGI app with Postgres overrides."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────────────── #
# Users & authentication
# ─────────────────────────────────────────────────────────────────────────── #


@pytest.fixture
async def test_user(db_session):
    """A persisted regular user (role ``user``)."""
    repo = UserRepository(db_session)
    return await repo.create(
        UserCreate(
            email=f"user-{uuid.uuid4().hex[:8]}@example.com",
            username=f"tester-{uuid.uuid4().hex[:8]}",
            password=TEST_PASSWORD,
            full_name="Test User",
        )
    )


@pytest.fixture
async def admin_user(db_session):
    """A persisted admin user (role ``admin``)."""
    from backend.app.domain.user.models import User
    from sqlmodel import select

    user = User(
        email=f"admin-{uuid.uuid4().hex[:8]}@example.com",
        username=f"admin-{uuid.uuid4().hex[:8]}",
        hashed_password=security.get_password_hash(TEST_PASSWORD),
        full_name="Admin User",
        role="admin",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    assert (await db_session.exec(select(User).where(User.id == user.id))).first()
    return user


@pytest.fixture
def user_auth_headers(test_user):
    """Bearer token headers for ``test_user``."""
    token = security.create_access_token(
        test_user.id,
        role=test_user.role,
        additional_claims={"email": test_user.email},
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_auth_headers(admin_user):
    """Bearer token headers for ``admin_user``."""
    token = security.create_access_token(
        admin_user.id,
        role="admin",
        additional_claims={"email": admin_user.email},
    )
    return {"Authorization": f"Bearer {token}"}
