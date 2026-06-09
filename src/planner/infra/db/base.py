"""SQLAlchemy async Base and session factory configuration."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""

    pass


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    Args:
        database_url: PostgreSQL connection URL (e.g., postgresql+asyncpg://user:pass@host/db)

    Returns:
        AsyncEngine instance configured for async operations
    """
    return create_async_engine(database_url, echo=False, pool_pre_ping=True)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory.

    Args:
        engine: AsyncEngine instance from create_engine()

    Returns:
        async_sessionmaker that produces AsyncSession instances
    """
    return async_sessionmaker(engine, expire_on_commit=False)
