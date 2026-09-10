"""LLM (OpenAI-compatible endpoint) settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``LLM__API_URL=llm-service``; unset fields
fall back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """OpenAI-compatible LLM endpoint used by the analyzer.

    ``LLM__API_URL``, ``LLM__API_KEY``, ``LLM__MODEL_ID``,
    ``LLM__SAMPLING_PARAMETERS`` (a JSON object, e.g.
    ``LLM__SAMPLING_PARAMETERS='{"temperature": 0.2}'``),
    ``LLM__SUMMARIZATION_PROMPT_NAME``.
    """

    model_config = SettingsConfigDict(
        env_prefix="LLM__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_url: str = "llm-service"  # docker-compose service name / base URL
    api_key: str | None = None
    model_id: str = "gpt-4o-mini"
    sampling_parameters: dict = {}  # e.g. {"temperature": 0.2}
    summarization_prompt_name: str = "lecture-summary"  # Langfuse prompt name
