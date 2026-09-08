"""Application settings shared across swot-bot services (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for a swot-bot service.

    All fields map to ``.env`` / environment variables (see docs). Only the
    fields relevant to a given service are actually consumed, but keeping one
    shared model avoids fragmentation.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram (bot) ---
    bot_token: str | None = None
    admin_id: int | None = None
    target_chat_id: int | None = None

    # --- Broker ---
    rabbit_url: str = "amqp://guest:guest@rabbitmq:5672/"

    # --- Paths (shared volumes) ---
    media_dir: str = "/data/media"
    artifacts_dir: str = "/data/artifacts"

    # --- Downloader ---
    allowed_sources: str = ""  # empty = any source
    max_video_duration_sec: int = 7200

    # --- Transcriber ---
    whisper_model: str = "distil-large-v3"
    whisper_device: str = "cuda"  # cpu | cuda
    whisper_compute_type: str = "int8_float16"
    whisper_language: str = ""
    whisper_batch_size: int = 4

    # --- LLM (analyzer, OpenAI-compatible) ---
    openai_compatible_api_url: str = "https://api.openai.com/v1"
    openai_compatible_api_key: str | None = None
    llm_model_id: str = "gpt-4o-mini"
    llm_temperature: float = 0.3
    prompt_name: str = "lecture-summary"

    # --- Langfuse ---
    langfuse_base_url: str = "http://langfuse:3000"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None

    # --- Observability ---
    log_level: str = "INFO"
    health_port: int = 8080

    # --- Lifecycle ---
    artifacts_retention_hours: int = 168

    # --- Model dir for faster-whisper cache ---
    whisper_models_dir: str = "/data/models"


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
