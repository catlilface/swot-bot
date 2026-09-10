"""Dishka providers wiring shared components (Settings, HealthServer)."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import Settings, get_settings

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
    """Provide an aiohttp HealthServer on HEALTH_PORT (APP-scoped)."""

    @provide(scope=Scope.APP)
    async def health_server(self, settings: Settings) -> AsyncIterable[HealthServer]:
        server = HealthServer(port=settings.health_port)
        yield server
