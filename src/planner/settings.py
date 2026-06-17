from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-insecure-change-me"


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str
    """PostgreSQL connection URL, e.g. postgresql+asyncpg://planner:planner@localhost:5432/planner"""

    # Redis
    redis_url: str
    """Redis connection URL, e.g. redis://localhost:6379/0"""

    # Telegram
    bot_token: str
    """Telegram bot token from BotFather"""

    team_chat_id: int
    """Telegram chat ID for team notifications"""

    admin_ids: str = ""
    """Comma-separated Telegram user IDs allowed to mutate plans (spec section 16)."""

    # LLM
    anthropic_api_key: str = ""
    """Anthropic API key for Claude models (optional — falls back to BasicIntentParser)"""

    agent_enabled: bool = True
    """Use the Claude tool-use PlannerAgent (when a key is set). Off → legacy enum path."""

    # Web
    webhook_secret: str = ""
    """Secret for webhook validation (optional)"""

    jwt_secret: str = DEFAULT_JWT_SECRET
    """Secret used to sign admin-session JWTs (spec section 9.2)."""

    # Notion
    notion_token: str = ""
    """Notion internal-integration token (optional — Notion sync disabled when empty)."""

    notion_database_id: str = ""
    """Target Notion database id for captured tasks (optional)."""

    # App
    timezone: str = "Europe/Moscow"
    """Application timezone"""

    debug: bool = False
    """Debug mode flag"""

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.split(",") if x.strip()}


def get_settings() -> Settings:
    """Get application settings instance."""
    return Settings()  # type: ignore[call-arg]  # values populated from env / .env


def ensure_secure_config(settings: Settings) -> None:
    """Refuse to run in production with security-critical defaults left unset.

    Raises RuntimeError when DEBUG is false and JWT_SECRET is still the public
    default — an attacker who knows the default can forge admin sessions.
    """
    if not settings.debug and settings.jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is the insecure default. Set a strong JWT_SECRET "
            '(generate one with: python -c "import secrets; '
            "print(secrets.token_urlsafe(32))\") or run with DEBUG=true for "
            "local development only."
        )
