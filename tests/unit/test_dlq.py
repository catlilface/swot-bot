"""DLQ behaviour on RabbitMQ (P1-3).

Requires a live broker: by default the dev compose stack at
``amqp://swot:swot@localhost:5672/`` (override with ``SWOT_TEST_RABBIT_URL``).
Skipped when the broker is unreachable so the unit suite stays green anywhere.
"""

import asyncio
import contextlib
import json
import os
import time
from uuid import uuid4

import aio_pika
import pytest
from aio_pika.exceptions import AMQPChannelError
from swot_bus.rabbit import (
    EXCHANGE_NAME,
    RabbitMessageBus,
    dlq_name_for,
)

DEFAULT_RABBIT_URL = "amqp://swot:swot@localhost:5672/"


def _rabbit_url() -> str:
    return os.environ.get("SWOT_TEST_RABBIT_URL", DEFAULT_RABBIT_URL)


async def _broker_available(url: str) -> bool:
    try:
        conn = await asyncio.wait_for(aio_pika.connect_robust(url), timeout=3)
    except Exception:  # noqa: BLE001
        return False
    await conn.close()
    return True


async def test_poison_message_lands_in_dlq_after_retries() -> None:
    """Сообщение, ломающее десериализацию → после N попыток в <queue>.dlq."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    suffix = uuid4().hex[:8]
    queue = f"t12-poison-{suffix}"
    dlq = dlq_name_for(queue)
    handler_calls = 0

    async def handler(message) -> None:  # type: ann
        nonlocal handler_calls
        handler_calls += 1

    bus = RabbitMessageBus(
        url, queue_name=queue, routing_keys=["download.request"], max_retries=1
    )
    await bus.connect()
    consume_task = asyncio.create_task(bus.consume(handler))
    try:
        # Ядовитое сообщение: валидный msg_type, но payload не проходит
        # model_validate (task_id не UUID) → десериализация ломается.
        poison = json.dumps(
            {
                "msg_type": "download.request",
                "task_id": "not-a-uuid",
                "trace_id": "t-poisone2e",
                "source": {"url": "https://example.com/x.mp4"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            exchange = await channel.declare_exchange(
                EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
            )
            await exchange.publish(
                aio_pika.Message(body=poison, content_type="application/json"),
                routing_key="download.request",
            )

        # Ждём, пока сообщение не окажется в DLQ (retry → dead-letter).
        body = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and body is None:
            async with await aio_pika.connect_robust(url) as probe:
                channel = await probe.channel()
                q = await channel.declare_queue(dlq, durable=True)
                raw = await q.get(timeout=2, fail=False)
                if raw is not None:
                    body = raw.body
                    await raw.ack()
            await asyncio.sleep(0.2)

        assert body == poison, "poison message must be preserved in the DLQ"
        assert handler_calls == 0, "poison message must never reach the handler"
    finally:
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consume_task
        await bus.close()
        await _purge_queues(url, queue, dlq)


async def _purge_queues(url: str, queue: str, dlq: str) -> None:
    """Best-effort removal of test queues so reruns stay clean."""
    try:
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            for name in (queue, dlq):
                try:
                    await channel.queue_delete(name)
                except AMQPChannelError:
                    pass  # NOT_FOUND: already gone
            # DLX survives (shared); leave it in place.
    except Exception:  # noqa: BLE001
        pass
