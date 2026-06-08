"""
Application configuration loaded from environment variables (.env).

Exposes a single `settings` singleton built on top of
`pydantic_settings.BaseSettings`. Every other module must read
configuration through `settings`, never via `os.environ` directly
or hardcoded values (see Rules.md section 13).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings sourced from `.env`."""

    # Database
    DATABASE_URL: str

    # JWT / Security
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # Redis (token blacklist, login-attempt counters)
    REDIS_URL: str

    # Application
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # CORS - comma-separated list of allowed origins
    CORS_ORIGINS: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS_ORIGINS string into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (loaded once per process)."""
    return Settings()


settings = get_settings()
