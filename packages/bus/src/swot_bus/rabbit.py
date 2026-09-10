"""RabbitMQ MessageBus implementation on aio-pika.

Publishes all pipeline messages to a single exchange 'swot.events' with the
msg_type as routing key. Each consumer binds the exchange to its own queue.

Dead-lettering (P1-3): every service queue is declared with
``x-dead-letter-exchange``/``x-dead-letter-routing-key`` pointing at
``<exchange>.dlx``; dead messages land in ``<queue>.dlq`` so nothing is lost.
A failed message is republished up to ``max_retries`` times with an
incremented ``swot-attempts`` header; once the budget is exhausted the
message is dead-lettered (nack without requeue) and the broker routes it
to the DLQ.

Design follows docs/architecture.md: lightweight messages only (paths + ids);
heavy artifacts live on the shared volume.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustConnection
from aio_pika.exceptions import AMQPChannelError
from aio_pika.pool import Pool
from swot_contracts import BaseMessage

logger = logging.getLogger(__name__)

TRACE_ID_HEADER = "swot-trace-id"
ATTEMPTS_HEADER = "swot-attempts"

EXCHANGE_NAME = "swot.events"
RESULT_QUEUE = "result.deliver"
JOB_EVENTS_QUEUE = "job.events"


def dlx_name_for(exchange_name: str) -> str:
    """Dead-letter exchange name for a main exchange."""
    return f"{exchange_name}.dlx"


def dlq_name_for(queue_name: str) -> str:
    """Dead-letter queue name for a service queue."""
    return f"{queue_name}.dlq"


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
        max_retries: int = 3,
    ) -> None:
        self._url = url
        self._exchange_name = exchange_name
        self._queue_name = queue_name
        self._routing_keys = routing_keys or [queue_name]
        self._prefetch = prefetch
        self._max_retries = max_retries
        self._dlx_name = dlx_name_for(exchange_name)
        self._connection: AbstractRobustConnection | None = None
        self._channel_pool: Pool | None = None
        self._exchange: Any = None
        self._dlx: Any = None
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
            self._dlx = await channel.declare_exchange(
                self._dlx_name, aio_pika.ExchangeType.TOPIC, durable=True
            )
            self._job_events_exchange = await channel.declare_exchange(
                "job.events", aio_pika.ExchangeType.FANOUT, durable=True
            )
        return self

    async def is_ready(self) -> bool:
        """Readiness probe: a live (not closed) connection to the broker."""
        return self._connection is not None and not self._connection.is_closed

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
        """Bind a durable queue (with DLQ wiring) and dispatch to handler.

        Long-running: declares ``<queue>`` with dead-letter arguments and
        ``<queue>.dlq`` bound to the DLX, then dispatches every message.
        """
        if self._channel_pool is None:
            msg = "bus not connected"
            raise RuntimeError(msg)
        async with self._channel_pool.acquire() as channel:
            queue = await self._declare_service_queue(channel)
            async for message in queue.iterator():
                task = asyncio.create_task(self._dispatch(message, handler))
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

    async def _declare_service_queue(self, channel: Any) -> Any:
        """Declare service queue + DLQ with DLX wiring.

        If the queue already exists with different arguments (e.g. it was
        created before the DLQ wiring was added), RabbitMQ closes the
        channel with PRECONDITION_FAILED. We delete the stale queue and
        re-declare, so rolling out the DLQ does not brick the stack.
        """
        arguments = {
            "x-dead-letter-exchange": self._dlx_name,
            "x-dead-letter-routing-key": self._queue_name,
        }
        try:
            return await self._declare_once(channel, arguments)
        except AMQPChannelError:
            logger.warning(
                "queue %s predates the DLQ wiring (stale arguments); deleting "
                "and re-declaring",
                self._queue_name,
            )
            # The channel used above was closed at the broker level, so the
            # migration (delete + re-declare) runs on a fresh channel.
            async with self._channel_pool.acquire() as fresh:
                await self._delete_quietly(fresh, self._queue_name)
                await self._delete_quietly(fresh, dlq_name_for(self._queue_name))
                return await self._declare_once(fresh, arguments)

    @staticmethod
    async def _delete_quietly(channel: Any, name: str) -> None:
        """Delete a queue, tolerating NOT_FOUND (already migrated elsewhere)."""
        try:
            await channel.queue_delete(name)
        except AMQPChannelError:
            logger.debug("queue %s not found, nothing to delete", name)

    async def _declare_once(self, channel: Any, arguments: dict[str, str]) -> Any:
        queue = await channel.declare_queue(
            self._queue_name,
            durable=True,
            arguments=arguments,
        )
        for key in self._routing_keys:
            await queue.bind(self._exchange, key)
        dlq = await channel.declare_queue(dlq_name_for(self._queue_name), durable=True)
        await dlq.bind(self._dlx, self._queue_name)
        return queue

    async def _dispatch(
        self,
        raw: AbstractIncomingMessage,
        handler: Callable[[BaseMessage], Awaitable[None]],
    ) -> None:
        from .serialization import deserialize

        headers = raw.headers or {}
        try:
            attempts = int(headers.get(ATTEMPTS_HEADER, "0"))
        except (TypeError, ValueError):
            attempts = 0
        try:
            trace_id = headers.get(TRACE_ID_HEADER)
            if trace_id:
                self._bind_trace(trace_id)
            message = deserialize(raw.body)
            self._bind_message_context(message)
            await handler(message)
            await raw.ack()
        except Exception:
            if attempts < self._max_retries:
                # Retry within budget: republish with incremented attempts,
                # then ack the original (the broker requeue would keep the
                # stale header).
                await self._republish_with_retry(raw, headers, attempts)
            else:
                # Budget exhausted: dead-letter to <queue>.dlq via x-dead-letter-*.
                await raw.nack(requeue=False)
                logger.exception(
                    "message dead-lettered after %d attempt(s): queue=%s",
                    attempts + 1,
                    self._queue_name,
                )
            return

    async def _republish_with_retry(
        self, raw: AbstractIncomingMessage, headers: Any, attempts: int
    ) -> None:
        try:
            retry_headers = dict(headers)
            retry_headers[ATTEMPTS_HEADER] = str(attempts + 1)
            content_type = raw.content_type
            await self._exchange.publish(
                aio_pika.Message(
                    body=raw.body,
                    content_type=str(content_type)
                    if content_type
                    else "application/json",
                    headers=retry_headers,
                ),
                routing_key=raw.routing_key,
            )
            await raw.ack()
            logger.warning(
                "message failed, retrying (attempt %d/%d): queue=%s",
                attempts + 1,
                self._max_retries,
                self._queue_name,
            )
        except Exception:  # noqa: BLE001
            # Republish failed: dead-letter the original so it is not lost.
            logger.exception("retry republish failed; dead-lettering original")
            await raw.nack(requeue=False)

    @staticmethod
    def _bind_trace(trace_id: str) -> None:
        """Restore trace_id in structlog context so logs keep one trace."""
        try:
            import structlog.contextvars  # noqa: PLC0415

            structlog.contextvars.bind_contextvars(trace_id=trace_id)
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    def _bind_message_context(message: BaseMessage) -> None:
        """Bind the message's task_id/trace_id into structlog contextvars.

        Runs in the per-message dispatch task, so the binding lives exactly
        for the duration of the handler — the consumer side of the
        ``swot-trace-id`` round-trip (T-1.5): logs of the whole handler, incl.
        stdlib loggers, carry the same trace_id/task_id as the publisher.
        No-op without structlog.
        """
        try:
            import structlog.contextvars  # noqa: PLC0415

            structlog.contextvars.bind_contextvars(
                task_id=str(message.task_id),
                trace_id=message.trace_id,
            )
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
