"""In-memory MessageBus for tests (implements contracts.MessageBus)."""

from collections.abc import Awaitable, Callable
from typing import Any

from swot_contracts import BaseMessage


class FakeBus:
    """Routes published messages to registered in-memory consumers.

    With ``capture=True`` also records all published messages for assertions.
    """

    def __init__(self, capture: bool = True) -> None:
        self.capture = capture
        self.published: list[BaseMessage] = []
        self._handler: Callable[[BaseMessage], Awaitable[None]] | None = None

    async def publish(self, message: BaseMessage) -> None:
        if self.capture:
            self.published.append(message)
        if self._handler is not None:
            await self._handler(message)

    async def consume(self, handler: Callable[[BaseMessage], Awaitable[None]]) -> None:
        self._handler = handler

    async def close(self) -> None:
        self._handler = None

    def published_types(self) -> list[Any]:
        return [m.msg_type for m in self.published]

    def get(self, index: int = 0) -> BaseMessage:
        return self.published[index]
