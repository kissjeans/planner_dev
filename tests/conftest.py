"""Root conftest: testcontainers Postgres fixture shared by integration and e2e tests."""

from __future__ import annotations

import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from planner.infra.db.base import create_session_factory

# Docker Desktop on macOS uses a non-standard socket path
_DOCKER_DESKTOP_SOCK = os.path.expanduser("~/.docker/run/docker.sock")
if os.path.exists(_DOCKER_DESKTOP_SOCK) and "DOCKER_HOST" not in os.environ:
    os.environ["DOCKER_HOST"] = f"unix://{_DOCKER_DESKTOP_SOCK}"


def _docker_available() -> bool:
    try:
        import docker
        docker.from_env().ping()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_container():
    """Start a real Postgres 16 container for the test session."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping testcontainers tests")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_url(pg_container) -> str:
    """asyncpg URL for the test Postgres container."""
    url = pg_container.get_connection_url()
    return url.replace("psycopg2", "asyncpg").replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def run_migrations(pg_url: str) -> None:
    """Apply alembic migrations once per session."""
    env = {**os.environ, "DATABASE_URL": pg_url}
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"Migration failed:\n{result.stderr}"


@pytest_asyncio.fixture(scope="session")
async def db_engine(pg_url: str, run_migrations):
    """Session-scoped async engine with NullPool — each session opens its own connection,
    preventing asyncpg protocol state from bleeding between tests."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    engine = create_async_engine(pg_url, echo=False, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest_asyncio.fixture
async def db_session(db_session_factory) -> AsyncSession:
    """Per-test async session with automatic rollback."""
    async with db_session_factory() as session, session.begin():
        yield session
        await session.rollback()
