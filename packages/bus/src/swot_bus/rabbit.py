"""RabbitMQ MessageBus implementation on aio-pika.

Publishes all pipeline messages to a single exchange 'swot.events' with the
msg_type as routing key. Each consumer binds the exchange to its own queue.

Design follows docs/architecture.md: lightweight messages only (paths + ids);
heavy artifacts live on the shared volume.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection
from aio_pika.pool import Pool
from swot_contracts import BaseMessage

logger = None  # replaced by a logger in provider wiring

EXCHANGE_NAME = "swot.events"
RESULT_QUEUE = "result.deliver"
JOB_EVENTS_QUEUE = "job.events"


class RabbitMessageBus:
    """Publish messages by msg_type; consume bound handlers."""

    def __init__(
        self,
        url: str,
        exchange_name: str = EXCHANGE_NAME,
        queue_name: str = RESULT_QUEUE,
        prefetch: int = 1,
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
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
            aio_pika.Message(body=body, content_type="application/json"),
            routing_key=message.msg_type.value,
        )

    async def consume(self, handler: Callable[[BaseMessage], Awaitable[None]]) -> None:
        """Bind a durable queue and dispatch messages to handler (long-running)."""
        if self._channel_pool is None:
            msg = "bus not connected"
            raise RuntimeError(msg)
        async with self._channel_pool.acquire() as channel:
            queue = await channel.declare_queue(self._queue_name, durable=True)
            await queue.bind(self._exchange, self._queue_name)
            await queue.bind(self._exchange, "job.#")
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
            message = deserialize(raw.body)
            await handler(message)
            await raw.ack()
        except Exception:
            # failure -> reject; DLQ/retry handled at broker level
            await raw.nack(requeue=False)
            if logger is not None:
                logger.exception("message processing failed")

    async def close(self) -> None:
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        if self._channel_pool is not None:
            await self._channel_pool.close()
        if self._connection is not None:
            await self._connection.close()


async def open_bus(url: str, **kwargs: Any) -> RabbitMessageBus:
    bus = RabbitMessageBus(url, **kwargs)
    return await bus.connect()
