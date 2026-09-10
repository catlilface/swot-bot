"""Test doubles for the message bus (kept out of production code, T-2.3).

Import as ``from fakes import FakeBus, FakeBusProvider`` — the ``tests/unit``
directory is on ``sys.path`` during pytest runs (rootdir import mode).
"""

from collections.abc import Awaitable, Callable

from dishka import Provider, Scope, provide
from swot_contracts import BaseMessage, MessageBus


class FakeBus:
    """In-memory MessageBus: routes published messages to registered consumers.

    With ``capture=True`` also records all published messages for assertions.
    """

    def __init__(self, capture: bool = True) -> None:
        self.capture = capture
        self.published: list[BaseMessage] = []
        self.handler: Callable[[BaseMessage], Awaitable[None]] | None = None

    async def publish(self, message: BaseMessage) -> None:
        if self.capture:
            self.published.append(message)
        if self.handler is not None:
            await self.handler(message)

    async def consume(self, handler: Callable[[BaseMessage], Awaitable[None]]) -> None:
        self.handler = handler

    async def is_ready(self) -> bool:
        """The in-memory bus is always "connected"."""
        return True

    async def close(self) -> None:
        self.handler = None

    def published_types(self) -> list:
        return [m.msg_type for m in self.published]

    def get(self, index: int = 0) -> BaseMessage:
        return self.published[index]


class FakeBusProvider(Provider):
    """Dishka provider: in-memory MessageBus instead of RabbitMQ (tests only)."""

    @provide(scope=Scope.APP)
    def bus(self) -> MessageBus:
        return FakeBus()
