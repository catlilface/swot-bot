"""Dishka provider wiring observability components (HealthServer)."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import Settings

from .health import HealthServer


class ObservabilityProvider(Provider):
    """Provide an aiohttp HealthServer on HEALTH_PORT (APP-scoped)."""

    @provide(scope=Scope.APP)
    async def health_server(self, settings: Settings) -> AsyncIterable[HealthServer]:
        server = HealthServer(port=settings.health_port)
        yield server
