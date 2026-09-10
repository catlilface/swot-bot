"""Transcriber sub-settings (OpenAI-compatible ASR endpoint).

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``TRANSCRIBER__API_URL=asr-service``; unset
fields fall back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TranscriberSettings(BaseSettings):
    """OpenAI-compatible ASR endpoint used by the transcriber (TRANSCRIBER__*).

    ``TRANSCRIBER__API_URL``, ``TRANSCRIBER__API_KEY``, ``TRANSCRIBER__MODEL``,
    ``TRANSCRIBER__LANGUAGE``, ``TRANSCRIBER__SEGMENT_DURATION_SEC``,
    ``TRANSCRIBER__MAX_AUDIO_MB``, ``TRANSCRIBER__ASR_TIMEOUT_SEC``.
    """

    model_config = SettingsConfigDict(
        env_prefix="TRANSCRIBER__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_url: str = "asr-service"  # OpenAI-compatible base URL (e.g. http://asr:8000/v1)
    api_key: str | None = None  # Bearer key; "none" is sent when unset
    model: str = "whisper-1"  # ASR model id exposed by the endpoint
    language: str = ""  # ISO code, e.g. "ru"; empty = auto-detect
    segment_duration_sec: int = (
        600  # длинное аудио режется на чанки до N сек (10–15 мин)
    )
    max_audio_mb: int = (
        512  # жёсткий лимит размера аудио (проверка ДО загрузки в память)
    )
    asr_timeout_sec: int = 300  # таймаут одного вызова ASR-эндпоинта
