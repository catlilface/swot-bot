"""Application settings shared across services.

One shared :class:`Settings` model is read from the process environment / ``.env``
by every service. Scalar fields map to their own env vars; sub-settings groups
are optional (default instances) and are populated from ``SECTION__FIELD`` env
vars (the ``__`` delimiter), e.g. ``BROKER__HOST=rabbitmq`` — see each
sub-settings class.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .broker_settings import BrokerSettings
from .downloader_settings import DownloaderSettings
from .langfuse_settings import LangfuseSettings
from .llm_settings import LLMSettings
from .telegram_settings import TelegramSettings
from .transcriber_settings import TranscriberSettings


class Settings(BaseSettings):
    """Environment-driven configuration for a swot-bot service.

    Fields map to the process environment or ``.env``:
    - scalar fields (``media_dir``, ``artifacts_dir``, ``health_port``) map to
      their own env vars (``MEDIA_DIR``, ``ARTIFACTS_DIR``, ``HEALTH_PORT``);
    - sub-settings groups are **optional** and default to a fully defaulted
      instance; when set, they are populated from ``SECTION__FIELD`` env vars
      (the ``env_nested_delimiter="__"`` delimiter), e.g.
      ``BROKER__HOST=rabbitmq``. Each sub-settings class also declares the
      matching ``env_prefix`` (``BROKER__``, ``LLM__``, …) so that
      ``BrokerSettings()`` works standalone.

    Keeping one shared model for all services avoids config fragmentation;
    each service only reads the fields relevant to it.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )

    media_dir: str = "/data/media"
    artifacts_dir: str = "/data/artifacts"
    health_port: int = 8080
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    downloader: DownloaderSettings = Field(default_factory=DownloaderSettings)
    langfuse: LangfuseSettings = Field(default_factory=LangfuseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    transcriber: TranscriberSettings = Field(default_factory=TranscriberSettings)


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached :class:`Settings` singleton.

    Instantiate once at startup (composition root) and reuse; the first call
    reads the environment / ``.env`` and the result is memoised.
    """
    return Settings()


__all__ = [
    "BrokerSettings",
    "DownloaderSettings",
    "LLMSettings",
    "LangfuseSettings",
    "Settings",
    "TelegramSettings",
    "TranscriberSettings",
    "get_settings",
]
