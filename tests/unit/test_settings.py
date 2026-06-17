import pytest
from pydantic import ValidationError

from planner.settings import Settings, get_settings


class TestSettingsValidation:
    """Test Settings model validation."""

    def test_settings_loads_with_all_required_fields(self, monkeypatch):
        """Test that Settings loads correctly with all required fields."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        settings = Settings()

        assert settings.database_url == "postgresql+asyncpg://user:pass@localhost/db"
        assert settings.redis_url == "redis://localhost:6379/0"
        assert settings.bot_token == "test_token"
        assert settings.team_chat_id == 123456789
        assert settings.anthropic_api_key == "sk-ant-test"

    def test_settings_missing_database_url_raises_validation_error(self, monkeypatch):
        """Test that missing DATABASE_URL raises ValidationError."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")

        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_settings_missing_redis_url_raises_validation_error(self, monkeypatch):
        """Test that missing REDIS_URL raises ValidationError."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")

        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_settings_missing_bot_token_raises_validation_error(self, monkeypatch):
        """Test that missing BOT_TOKEN raises ValidationError."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.delenv("BOT_TOKEN", raising=False)
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")

        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_settings_missing_team_chat_id_raises_validation_error(self, monkeypatch):
        """Test that missing TEAM_CHAT_ID raises ValidationError."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.delenv("TEAM_CHAT_ID", raising=False)

        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_settings_missing_anthropic_api_key_defaults_to_empty(self, monkeypatch):
        """anthropic_api_key is optional; absent means BasicIntentParser is used."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        s = Settings(_env_file=None)
        assert s.anthropic_api_key == ""

    def test_settings_with_optional_fields_defaults(self, monkeypatch):
        """Test that optional fields have correct default values."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("TIMEZONE", raising=False)
        monkeypatch.delenv("DEBUG", raising=False)

        settings = Settings(_env_file=None)

        assert settings.webhook_secret == ""
        assert settings.timezone == "Europe/Moscow"
        assert settings.debug is False

    def test_settings_with_custom_optional_fields(self, monkeypatch):
        """Test that optional fields can be overridden."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("WEBHOOK_SECRET", "secret123")
        monkeypatch.setenv("TIMEZONE", "America/New_York")
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings()

        assert settings.webhook_secret == "secret123"
        assert settings.timezone == "America/New_York"
        assert settings.debug is True

    def test_team_chat_id_validates_as_integer(self, monkeypatch):
        """Test that TEAM_CHAT_ID is properly coerced to integer."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "987654321")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        settings = Settings()

        assert settings.team_chat_id == 987654321
        assert isinstance(settings.team_chat_id, int)

    def test_debug_flag_coerces_from_string(self, monkeypatch):
        """Test that DEBUG flag is properly coerced from string."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DEBUG", "1")

        settings = Settings()

        assert settings.debug is True

    def test_get_settings_returns_settings_instance(self, monkeypatch):
        """Test that get_settings() returns a Settings instance."""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("BOT_TOKEN", "test_token")
        monkeypatch.setenv("TEAM_CHAT_ID", "123456789")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        settings = get_settings()

        assert isinstance(settings, Settings)
        assert settings.bot_token == "test_token"


class TestEnsureSecureConfig:
    def _base(self, **overrides: object) -> Settings:
        from planner.settings import Settings
        kwargs: dict[str, object] = dict(
            database_url="x", redis_url="x", bot_token="t", team_chat_id=1,
        )
        kwargs.update(overrides)
        return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]

    def test_default_secret_in_production_raises(self) -> None:
        from planner.settings import ensure_secure_config
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            ensure_secure_config(self._base(debug=False))

    def test_default_secret_in_debug_is_allowed(self) -> None:
        from planner.settings import ensure_secure_config
        ensure_secure_config(self._base(debug=True))  # no raise

    def test_custom_secret_in_production_is_allowed(self) -> None:
        from planner.settings import ensure_secure_config
        ensure_secure_config(self._base(debug=False, jwt_secret="a-strong-secret"))


def test_settings_notion_defaults_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("TEAM_CHAT_ID", "1")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    s = Settings(_env_file=None)
    assert s.notion_token == ""
    assert s.notion_database_id == ""
