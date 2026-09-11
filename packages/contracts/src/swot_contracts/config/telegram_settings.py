"""Telegram bot (AIogram) settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``TELEGRAM__TOKEN=123:ABC``; unset fields fall
back to the defaults below.
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseSettings):
    """AIogram bot credentials and targets for the bot service.

    ``TELEGRAM__TOKEN``, ``TELEGRAM__ADMIN_ID``, ``TELEGRAM__TARGET_CHAT_ID``,
    ``TELEGRAM__TARGET_TOPIC_ID`` (all optional).
    """

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: str | None = None  # BotFather token
    admin_id: int | None = None  # admin who submits links and receives results
    target_chat_id: int | None = None  # chat where ready summaries are published
    # Forum topic (message_thread_id) inside target_chat_id where ready
    # summaries are posted. Empty = the chat's default (General) topic.
    target_topic_id: int | None = None

    @field_validator("target_topic_id", mode="before")
    @classmethod
    def _empty_topic_is_none(cls, value: object) -> object:
        """Empty/whitespace env value means "no topic" (unsets the field)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Empty means the real https://api.telegram.org; set e.g.
    # http://fake-tg:8081 to point the bot at the dev Telegram stub.
    api_base_url: str = ""
