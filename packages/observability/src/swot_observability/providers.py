"""Dishka providers wiring shared components (Settings, HealthServer)."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings, get_settings

from .health import HealthServer


class SettingsProvider(Provider):
    """Provide the process-wide :class:`Settings` singleton (env / .env).

    Every service container registers this provider so that factories
    depending on :class:`Settings` resolve identically (``get_settings()``
    is lru-cached — one construction per process).
    """

    @provide(scope=Scope.APP)
    def settings(self) -> Settings:
        return get_settings()


class ObservabilityProvider(Provider):
    """Provide an aiohttp HealthServer on HEALTH_PORT (APP-scoped).

    ``/readyz`` reports ``degraded`` (503) until the :class:`MessageBus`
    readiness probe (``is_ready`` — for RabbitMQ: a live connection to the
    broker) turns true (T-1.6).
    """

    @provide(scope=Scope.APP)
    async def health_server(
        self, settings: Settings, bus: MessageBus
    ) -> AsyncIterable[HealthServer]:
        server = HealthServer(port=settings.health_port, readiness=bus.is_ready)
        yield server
