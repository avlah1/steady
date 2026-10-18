from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment variables, falling back to .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No default on purpose: the app refuses to start without a secret.
    api_key: str

    reply_timeout_s: float = 10.0
    ack_timeout_s: float = 30.0
    classify_timeout_s: float = 5.0

    # Browser origins allowed to call the API (the React dashboard), comma-separated.
    # Defaults cover the usual Vite and Create React App dev ports.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
