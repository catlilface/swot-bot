"""Langfuse (prompt templates + tracing) settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``LANGFUSE__HOST=langfuse``; unset fields fall
back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LangfuseSettings(BaseSettings):
    """Connection to the shared Langfuse instance.

    Used by the analyzer to fetch prompt templates (and tracing);
    ``public_key`` and ``secret_key`` are required.

    ``LANGFUSE__HOST``, ``LANGFUSE__PORT``, ``LANGFUSE__PUBLIC_KEY``,
    ``LANGFUSE__SECRET_KEY``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "langfuse"
    port: int = 3000
    public_key: str
    secret_key: str

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}"
