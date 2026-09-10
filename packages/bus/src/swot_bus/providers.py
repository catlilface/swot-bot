"""Dishka providers wiring MessageBus and JobRegistry implementations.

These providers expose the abstract contracts (MessageBus, JobRegistry) from
``swot_contracts.ports`` while keeping the concrete adapters swappable — tests
override the providers / adapters freely (see service tests).
"""

from collections.abc import AsyncIterable
from typing import Any

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .fake import FakeBus
from .jobs import InMemoryJobRegistry
from .rabbit import open_bus


class RabbitBusProvider(Provider):
    """Provide a RabbitMQ MessageBus for a specific service queue.

    Instantiate with ``RabbitBusProvider("video.download", ["download.request"])``
    so each service consumes only its own events.
    """

    def __init__(
        self,
        queue_name: str,
        routing_keys: list[str] | None = None,
        prefetch: int = 1,
    ) -> None:
        super().__init__()
        self._queue_name = queue_name
        self._routing_keys = routing_keys or [queue_name]
        self._prefetch = prefetch

    @provide(scope=Scope.APP)
    async def bus(self, settings: Settings) -> AsyncIterable[MessageBus]:
        bus = await open_bus(
            settings.broker.rabbit_url,
            queue_name=self._queue_name,
            routing_keys=self._routing_keys,
            prefetch=self._prefetch,
        )
        yield bus
        await bus.close()


# Backward-compatible alias: consumes all events on the shared result queue.
BusProvider = RabbitBusProvider


class RegistryProvider(Provider):
    """Provide an in-memory JobRegistry by default (no Redis)."""

    @provide(scope=Scope.APP, provides=JobRegistry)
    def registry(self) -> InMemoryJobRegistry:
        return InMemoryJobRegistry()


class FakeBusProvider(Provider):
    """Test/local provider: in-memory MessageBus instead of RabbitMQ."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    @provide(scope=Scope.APP)
    def bus(self) -> MessageBus:
        return FakeBus()
