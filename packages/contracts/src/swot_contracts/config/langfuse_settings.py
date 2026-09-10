"""Langfuse (prompt templates + tracing) settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``LANGFUSE__HOST=langfuse``; unset fields fall
back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LangfuseSettings(BaseSettings):
    """Connection to the shared Langfuse instance.

    Used by the analyzer to fetch prompt templates (and tracing);
    empty keys mean Langfuse is disabled (analyzer uses the local prompt).

    ``LANGFUSE__HOST``, ``LANGFUSE__PORT``, ``LANGFUSE__PUBLIC_KEY``,
    ``LANGFUSE__SECRET_KEY``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LANGFUSE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "langfuse"
    port: int = 3000
    # Empty keys mean "Langfuse is not in use": the analyzer falls back to
    # the local prompt (see swot_analyzer.prompts.LocalPromptProvider).
    public_key: str = ""
    secret_key: str = ""

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"
