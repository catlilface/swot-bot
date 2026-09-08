"""RabbitMQ MessageBus implementation on aio-pika.

Publishes all pipeline messages to a single exchange 'swot.events' with the
msg_type as routing key. Each consumer binds the exchange to its own queue.

Design follows docs/architecture.md: lightweight messages only (paths + ids);
heavy artifacts live on the shared volume.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection
from aio_pika.pool import Pool
from swot_contracts import BaseMessage

logger = logging.getLogger(__name__)

TRACE_ID_HEADER = "swot-trace-id"

EXCHANGE_NAME = "swot.events"
RESULT_QUEUE = "result.deliver"
JOB_EVENTS_QUEUE = "job.events"


class RabbitMessageBus:
    """Publish messages by msg_type; consume bound handlers.

    Each instance owns one durable queue bound to the given ``routing_keys``.
    A service configures its own queue so it receives only the events it needs
    (e.g. downloader listens only for ``download.request``).
    """

    def __init__(
        self,
        url: str,
        exchange_name: str = EXCHANGE_NAME,
        queue_name: str = RESULT_QUEUE,
        routing_keys: list[str] | None = None,
        prefetch: int = 1,
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
        self._routing_keys = routing_keys or [queue_name]
        self._prefetch = prefetch
        self._connection: AbstractRobustConnection | None = None
        self._channel_pool: Pool | None = None
        self._exchange: Any = None
        self._job_events_exchange: Any = None
        self._pending_tasks: set[asyncio.Task] = set()

    async def connect(self) -> "RabbitMessageBus":
        connection = await aio_pika.connect_robust(self._url)
        self._connection = connection
        channel_pool = Pool(self._open_channel, max_size=10)
        self._channel_pool = channel_pool
        async with channel_pool.acquire() as channel:
            self._exchange = await channel.declare_exchange(
                self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
            )
            self._job_events_exchange = await channel.declare_exchange(
                "job.events", aio_pika.ExchangeType.FANOUT, durable=True
            )
        return self

    async def _open_channel(self) -> Any:
        if self._connection is None:
            msg = "bus not connected"
            raise RuntimeError(msg)
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=self._prefetch)
        return channel

    async def publish(self, message: BaseMessage) -> None:
        from .serialization import serialize

        if self._exchange is None:
            msg = "bus not connected"
            raise RuntimeError(msg)
        body = serialize(message)
        await self._exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                headers=self._trace_headers(),
            ),
            routing_key=message.msg_type.value,
        )

    @staticmethod
    def _trace_headers() -> dict[str, str]:
        """Copy the current structlog trace_id into a message header."""
        try:
            import structlog.contextvars  # noqa: PLC0415

            ctx = structlog.contextvars.get_contextvars()
            trace_id = ctx.get("trace_id")
        except Exception:  # noqa: BLE001 - structlog not in this service
            trace_id = None
        return {TRACE_ID_HEADER: trace_id} if trace_id else {}

    async def consume(self, handler: Callable[[BaseMessage], Awaitable[None]]) -> None:
        """Bind a durable queue and dispatch messages to handler (long-running)."""
        if self._channel_pool is None:
            msg = "bus not connected"
            raise RuntimeError(msg)
        async with self._channel_pool.acquire() as channel:
            queue = await channel.declare_queue(self._queue_name, durable=True)
            for key in self._routing_keys:
                await queue.bind(self._exchange, key)
            async for message in queue.iterator():
                task = asyncio.create_task(self._dispatch(message, handler))
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

    async def _dispatch(
        self,
        raw: AbstractIncomingMessage,
        handler: Callable[[BaseMessage], Awaitable[None]],
    ) -> None:
        from .serialization import deserialize

        try:
            trace_id = (raw.headers or {}).get(TRACE_ID_HEADER)
            if trace_id:
                self._bind_trace(trace_id)
            message = deserialize(raw.body)
            await handler(message)
            await raw.ack()
        except Exception:
            # failure -> reject; DLQ/retry handled at broker level
            await raw.nack(requeue=False)
            logger.exception("message processing failed")

    @staticmethod
    def _bind_trace(trace_id: str) -> None:
        """Restore trace_id in structlog context so logs keep one trace."""
        try:
            import structlog.contextvars  # noqa: PLC0415

            structlog.contextvars.bind_contextvars(trace_id=trace_id)
        except Exception:  # noqa: BLE001
            return

    async def close(self) -> None:
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        if self._channel_pool is not None:
            await self._channel_pool.close()
        if self._connection is not None:
            await self._connection.close()


async def open_bus(
    url: str,
    *,
    queue_name: str = RESULT_QUEUE,
    routing_keys: list[str] | None = None,
    **kwargs: Any,
) -> RabbitMessageBus:
    bus = RabbitMessageBus(
        url, queue_name=queue_name, routing_keys=routing_keys, **kwargs
    )
    return await bus.connect()
