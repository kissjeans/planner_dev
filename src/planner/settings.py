from pydantic_settings import BaseSettings, SettingsConfigDict


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
    anthropic_api_key: str
    """Anthropic API key for Claude models"""

    openai_api_key: str
    """OpenAI API key for GPT models"""

    # Web
    webhook_secret: str = ""
    """Secret for webhook validation (optional)"""

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
    return Settings()
