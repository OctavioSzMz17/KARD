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

    # Equipos autorizados (cajas registradoras): cuánto dura la autorización de
    # una computadora y cuánto vive un QR antes de pedir uno nuevo.
    DEVICE_TTL_HOURS: int = 14
    CHALLENGE_TTL_SECONDS: int = 150

    # Redis (token blacklist, login-attempt counters)
    REDIS_URL: str

    # Application
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # CORS - comma-separated list of allowed origins
    CORS_ORIGINS: str

    # Stripe — suscripciones de los negocios hacia KARD.
    # Dejar vacío desactiva el cobro: los endpoints responden 503 en vez de
    # tronar, para que el resto de la plataforma siga funcionando sin claves.
    STRIPE_SECRET_KEY: str = ""
    # Firma de los webhooks (`whsec_...`). En local lo imprime `stripe listen`.
    STRIPE_WEBHOOK_SECRET: str = ""
    # Precios de cada plan, creados en el panel de Stripe (`price_...`).
    STRIPE_PRICE_STARTER: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_ENTERPRISE: str = ""

    # Resend — transactional email (leave empty to disable email sending)
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "KARD"
    APP_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def stripe_price_by_plan(self) -> dict[str, str]:
        """Plan → price de Stripe. Un plan sin precio configurado no se vende."""
        return {
            "starter": self.STRIPE_PRICE_STARTER,
            "pro": self.STRIPE_PRICE_PRO,
            "enterprise": self.STRIPE_PRICE_ENTERPRISE,
        }

    @property
    def billing_enabled(self) -> bool:
        """Sin clave secreta no hay cobro posible; los endpoints lo dicen claro."""
        return bool(self.STRIPE_SECRET_KEY)

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS_ORIGINS string into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (loaded once per process)."""
    return Settings()


settings = get_settings()
