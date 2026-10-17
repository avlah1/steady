from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment variables, falling back to .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No default on purpose: the app refuses to start without a secret.
    api_key: str

    reply_timeout_s: float = 10.0
    ack_timeout_s: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
