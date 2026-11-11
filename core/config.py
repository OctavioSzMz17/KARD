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

    # Cola de devoluciones de inventario (cancelar un pedido): cada cuántos segundos revisa si quedó
    # algo pendiente (reintentos y trabajos que un reinicio dejó a medias). 0 = no revisa.
    STOCK_RETURN_SWEEP_SECONDS: int = 30

    # Redis (token blacklist, login-attempt counters)
    REDIS_URL: str

    # Application
    # El nombre del producto tal como lo ven las personas (correos, título de la API).
    # Si la marca cambia, se cambia aquí y nada más.
    BRAND_NAME: str = "KARD"
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
    # Nombre que se ve como remitente. Vacío = usa BRAND_NAME.
    EMAIL_FROM_NAME: str = ""
    APP_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def email_sender_name(self) -> str:
        """Remitente visible: el nombre propio si se configuró, si no el de la marca."""
        return self.EMAIL_FROM_NAME or self.BRAND_NAME

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
