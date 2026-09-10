"""RabbitMQ broker connection settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``BROKER__HOST=rabbitmq``; unset fields fall
back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class BrokerSettings(BaseSettings):
    """Connection to the shared RabbitMQ broker for pipeline events.

    Env vars are prefixed (``env_prefix``): ``BROKER__HOST``,
    ``BROKER__PORT``, ``BROKER__USER``, ``BROKER__PASSWORD``. When nested
    inside :class:`~swot_contracts.config.Settings` the same names are
    produced via the parent's ``env_nested_delimiter="__"``.
    """

    model_config = SettingsConfigDict(
        env_prefix="BROKER__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "rabbitmq"
    port: int = 5672
    user: str = "guest"
    password: str = "guest"

    @property
    def rabbit_url(self) -> str:
        """AMQP URL for broker clients (e.g. aio-pika)."""
        return f"amqp://{self.user}:{self.password}@{self.host}:{self.port}/"
