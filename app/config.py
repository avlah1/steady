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

    # Optional: without it, replies are labelled by the keyword classifier instead of Claude.
    anthropic_api_key: str | None = None

    # Local Whisper model for the Box's audio replies: "base.en" (fast) or "small.en" (more
    # accurate, ~3x slower). Empty turns audio replies off (tests do this).
    stt_model: str = "base.en"
    # Max seconds to transcribe a reply before treating it as "unclear".
    stt_timeout_s: float = 4.0
    # MongoDB connection string. Empty: keep everything in memory only (lost on restart; tests do this).
    mongodb_uri: str | None = None
    mongodb_db: str = "steady"

    # Largest audio upload accepted. 1 MB is about 30 s of 16 kHz mono 16-bit audio.
    max_audio_bytes: int = 1_000_000

    # Browser origins allowed to call the API (the React dashboard), comma-separated.
    # Defaults cover the usual Vite and Create React App dev ports.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
