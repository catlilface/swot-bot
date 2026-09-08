"""Dishka providers wiring MessageBus and JobRegistry implementations.

These providers expose the abstract contracts (MessageBus, JobRegistry) from
``swot_contracts.ports`` while keeping the concrete adapters swappable — tests
override the providers / adapters freely (see service tests).
"""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings

from .fake import FakeBus
from .jobs import InMemoryJobRegistry
from .rabbit import open_bus


class BusProvider(Provider):
    """Provide a real RabbitMQ-backed MessageBus (APP-scoped, closes on exit)."""

    @provide(scope=Scope.APP)
    async def bus(self, settings: Settings) -> AsyncIterable[MessageBus]:
        bus = await open_bus(
            settings.rabbit_url,
            queue_name="result.deliver",
            prefetch=1,
        )
        yield bus
        await bus.close()


class RegistryProvider(Provider):
    """Provide an in-memory JobRegistry by default (no Redis)."""

    @provide(scope=Scope.APP)
    def registry(self) -> InMemoryJobRegistry:
        return InMemoryJobRegistry()


class FakeBusProvider(Provider):
    """Test/local provider: in-memory MessageBus instead of RabbitMQ."""

    @provide(scope=Scope.APP)
    def bus(self) -> MessageBus:
        return FakeBus()
