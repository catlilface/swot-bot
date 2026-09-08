"""Transcriber sub-settings (faster-whisper).

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``TRANSCRIBER__MODEL=turbo``; unset fields fall
back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TranscriberSettings(BaseSettings):
    """faster-whisper transcription options (TRANSCRIBER__*).

    ``TRANSCRIBER__MODEL``, ``TRANSCRIBER__LANGUAGE``, ``TRANSCRIBER__DEVICE``,
    ``TRANSCRIBER__COMPUTE_TYPE``, ``TRANSCRIBER__BATCH_SIZE``,
    ``TRANSCRIBER__MODELS_DIR``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model: str = "turbo"  # tiny | base | small | medium | large-v3 | turbo
    language: str = "en"  # ISO code, e.g. "ru"
    device: str = "cuda"  # cuda | cpu
    compute_type: str = "int8_float16"  # e.g. int8, int8_float16, float16, float32
    batch_size: int = 4  # transcription batch (GPU)
    models_dir: str = "/data/models"  # volume-mounted whisper model cache
