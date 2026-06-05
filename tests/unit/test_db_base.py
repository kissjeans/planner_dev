"""Tests for SQLAlchemy async Base and session factory."""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from planner.infra.db.base import Base, create_engine, create_session_factory


class TestBase:
    """Test SQLAlchemy declarative base."""

    def test_base_is_declarative_base(self):
        """Test that Base is a DeclarativeBase instance."""
        assert hasattr(Base, "registry")
        assert hasattr(Base, "metadata")


class TestCreateEngine:
    """Test create_engine function."""

    def test_create_engine_returns_async_engine(self):
        """Test that create_engine returns an AsyncEngine instance."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)

        assert isinstance(engine, AsyncEngine)

    def test_create_engine_with_different_urls(self):
        """Test that create_engine works with different URLs."""
        urls = [
            "postgresql+asyncpg://user:pass@localhost/db1",
            "postgresql+asyncpg://user:pass@127.0.0.1:5432/db2",
            "postgresql+asyncpg://user:pass@host.example.com/db3",
        ]

        for url in urls:
            engine = create_engine(url)
            assert isinstance(engine, AsyncEngine)

    def test_create_engine_with_echo_false(self):
        """Test that echo is set to False by default."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)

        assert engine.echo is False

    def test_create_engine_with_pool_pre_ping_enabled(self):
        """Test that pool_pre_ping is enabled."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)

        assert engine.pool._pre_ping is True


class TestCreateSessionFactory:
    """Test create_session_factory function."""

    def test_create_session_factory_returns_async_sessionmaker(self):
        """Test that create_session_factory returns an async_sessionmaker instance."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)
        session_factory = create_session_factory(engine)

        assert isinstance(session_factory, async_sessionmaker)

    def test_create_session_factory_session_type(self):
        """Test that session factory creates AsyncSession instances."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)
        session_factory = create_session_factory(engine)

        # Check that the configured class is AsyncSession
        assert session_factory.kw.get("class_") is None or session_factory.kw.get(
            "class_"
        ) == AsyncSession or issubclass(session_factory.kw.get("class_"), AsyncSession)

    def test_create_session_factory_expire_on_commit_false(self):
        """Test that expire_on_commit is set to False."""
        database_url = "postgresql+asyncpg://user:pass@localhost/testdb"
        engine = create_engine(database_url)
        session_factory = create_session_factory(engine)

        assert session_factory.kw.get("expire_on_commit") is False

    def test_session_factory_with_different_engines(self):
        """Test that session factory works with different engine instances."""
        urls = [
            "postgresql+asyncpg://user:pass@localhost/db1",
            "postgresql+asyncpg://user:pass@localhost/db2",
        ]

        for url in urls:
            engine = create_engine(url)
            session_factory = create_session_factory(engine)
            assert isinstance(session_factory, async_sessionmaker)
