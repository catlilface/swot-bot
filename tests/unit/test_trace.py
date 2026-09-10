"""T-1.5: сквозной trace_id/task_id и единый JSON-лог.

Критерии:
1. После ``bind_contextvars(trace_id=X)`` → ``bus.publish(...)`` кладёт
   ``swot-trace-id: X`` в AMQP header; потребитель биндит его в свой
   structlog-контекст (round-trip) — юнит (stub-аio_pika) и live-тест.
2. Stdlib-логгеры (aio_pika, openai, ...) пишут тот же JSON, что и
   structlog — все строки парсятся ``json.loads``.
3. Бот биндит trace_id/task_id до первого publish (``handle_link``).
"""

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from uuid import uuid4

import aio_pika
import pytest
import structlog
import structlog.contextvars
from swot_bus import InMemoryJobRegistry
from swot_bus.rabbit import RabbitMessageBus
from swot_bus.serialization import serialize
from swot_contracts import BaseMessage, DownloadRequest, MessageBus, SourceRef
from swot_contracts.ports import JobRegistry
from swot_observability import configure_logging

# ---------------------------------------------------------------------------
# Стуб aio_pika: без приватного доступа к RabbitMessageBus, monkeypatch на
# модульном уровне (connect_robust).
# ---------------------------------------------------------------------------


class _FakeIncoming:
    """Приходящее сообщение (подмножество API aio_pika.AbstractIncomingMessage)."""

    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self.body = body
        self.headers = headers
        self.routing_key = "download.request"
        self.content_type = "application/json"
        self.acked = False

    async def ack(self, *args: object, **kwargs: object) -> None:
        self.acked = True

    async def nack(self, *args: object, **kwargs: object) -> None:
        msg = "test message must not be nacked"
        raise AssertionError(msg)


class _FakeQueue:
    def __init__(self, name: str) -> None:
        self.name = name
        self.incoming: list[_FakeIncoming] = []

    async def bind(self, exchange: object, routing_key: str) -> None:
        pass

    async def set_qos(self, prefetch_count: int) -> None:
        pass

    async def queue_delete(self) -> None:
        pass

    def iterator(self) -> object:
        async def _gen() -> object:
            # Живёт, пока тест не отменит consume (как живой broker).
            while True:
                if self.incoming:
                    yield self.incoming.pop(0)
                else:
                    await asyncio.sleep(0.01)

        return _gen()


class _FakeExchange:
    def __init__(self) -> None:
        self.published: list[tuple[object, str]] = []
        self.connections: list[_FakeConnection] = []

    async def publish(self, message: object, routing_key: str = "") -> None:
        self.published.append((message, routing_key))


class _FakeChannel:
    def __init__(self, exchange: _FakeExchange) -> None:
        self._exchange = exchange
        self.queues: dict[str, _FakeQueue] = {}

    async def set_qos(self, prefetch_count: int | None = None) -> None:
        pass

    async def declare_exchange(
        self, name: str, exchange_type: object, durable: bool = False
    ) -> _FakeExchange:
        return self._exchange

    async def declare_queue(
        self, name: str, durable: bool = False, arguments: dict | None = None
    ) -> _FakeQueue:
        if name not in self.queues:
            self.queues[name] = _FakeQueue(name)
        return self.queues[name]

    async def queue_delete(self, name: str) -> None:
        self.queues.pop(name, None)

    async def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self, exchange: _FakeExchange) -> None:
        self._exchange = exchange
        self.channels: list[_FakeChannel] = []

    async def channel(self) -> _FakeChannel:
        channel = _FakeChannel(self._exchange)
        self.channels.append(channel)
        return channel

    def get_queue(self, name: str) -> _FakeQueue | None:
        for channel in self.channels:
            if name in channel.queues:
                return channel.queues[name]
        return None

    async def close(self) -> None:
        pass


def _make_stub_broker(monkeypatch: pytest.MonkeyPatch) -> _FakeExchange:
    """Подменить aio_pika.connect_robust: все подключения — в один stub."""
    exchange = _FakeExchange()

    async def fake_connect(url: str, *args: object, **kwargs: object) -> object:
        connection = _FakeConnection(exchange)
        exchange.connections.append(connection)
        return connection

    monkeypatch.setattr(aio_pika, "connect_robust", fake_connect)
    return exchange


# ---------------------------------------------------------------------------
# Критерий 1 (юнит): publish → header, consume → контекст (round-trip)
# ---------------------------------------------------------------------------


async def test_roundtrip_header_and_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _make_stub_broker(monkeypatch)
    structlog.contextvars.clear_contextvars()

    task_id = uuid4()
    trace_id = f"t-{uuid4().hex[:8]}"
    message = DownloadRequest(
        task_id=task_id,
        trace_id=trace_id,
        source=SourceRef(url="https://example.com/x.mp4", kind="generic"),
    )

    producer = RabbitMessageBus(
        "amqp://stub", queue_name="t15-prod", routing_keys=["download.request"]
    )
    await producer.connect()
    structlog.contextvars.bind_contextvars(trace_id=trace_id, task_id=str(task_id))
    try:
        await producer.publish(message)
    finally:
        structlog.contextvars.clear_contextvars()
    await producer.close()

    assert exchange.published, "message must reach the exchange"
    raw_message, routing_key = exchange.published[0]
    assert routing_key == "download.request"
    assert raw_message.headers.get("swot-trace-id") == trace_id

    # Consumer: то же сообщение → обработчик видит trace_id и task_id.
    body = serialize(message)
    seen: dict[str, str] = {}

    async def handler(consumed: BaseMessage) -> None:
        ctx = structlog.contextvars.get_contextvars()
        seen["trace_id"] = str(ctx.get("trace_id"))
        seen["task_id"] = str(ctx.get("task_id"))
        seen["type"] = type(consumed).__name__

    consumer = RabbitMessageBus(
        "amqp://stub", queue_name="t15-cons", routing_keys=["download.request"]
    )
    await consumer.connect()
    consume_task = asyncio.create_task(consumer.consume(handler))
    try:
        queue = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            queue = next(
                (
                    q
                    for conn in exchange.connections
                    if (q := conn.get_queue("t15-cons")) is not None
                ),
                None,
            )
            if queue is not None:
                break
            await asyncio.sleep(0.01)
        assert queue is not None, "consumer queue must be declared"
        queue.incoming.append(
            _FakeIncoming(body=body, headers=dict(raw_message.headers))
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not seen:
            await asyncio.sleep(0.01)
    finally:
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consume_task
        await consumer.close()

    assert seen.get("type") == "DownloadRequest"
    assert seen.get("trace_id") == trace_id, (
        "consumer must bind the producer's trace_id from the header"
    )
    assert seen.get("task_id") == str(task_id), (
        "consumer must bind task_id from the message"
    )


# ---------------------------------------------------------------------------
# Критерий 1 (live broker): round-trip через настоящий RabbitMQ (если есть).
# ---------------------------------------------------------------------------

DEFAULT_RABBIT_URL = "amqp://swot:swot@localhost:5672/"


def _rabbit_url() -> str:
    import os

    return os.environ.get("SWOT_TEST_RABBIT_URL", DEFAULT_RABBIT_URL)


async def _broker_available(url: str) -> bool:
    try:
        conn = await asyncio.wait_for(aio_pika.connect_robust(url), timeout=3)
    except Exception:  # noqa: BLE001
        return False
    await conn.close()
    return True


async def _queue_exists(url: str, name: str) -> bool:
    try:
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            await channel.declare_queue(name, passive=True)
        return True
    except Exception:  # noqa: BLE001
        return False


async def test_roundtrip_live_broker() -> None:
    """Полный круг: bind → publish → header → consume → контекст обработчика."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    suffix = uuid4().hex[:8]
    producer_q = f"t15-prod-{suffix}"
    consumer_q = f"t15-cons-{suffix}"
    task_id = uuid4()
    trace_id = f"t-{suffix}"
    seen: dict[str, str] = {}

    async def handler(consumed: BaseMessage) -> None:
        ctx = structlog.contextvars.get_contextvars()
        seen["trace_id"] = str(ctx.get("trace_id"))
        seen["task_id"] = str(ctx.get("task_id"))

    consumer = RabbitMessageBus(
        url, queue_name=consumer_q, routing_keys=["download.request"]
    )
    await consumer.connect()
    consume_task = asyncio.create_task(consumer.consume(handler))
    try:
        # Ожидать, пока очередь объявлена и привязана — иначе сообщение
        # уйдёт в пустоту (topic exchange, ни одной binding).
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not await _queue_exists(url, consumer_q):
            await asyncio.sleep(0.2)
        assert await _queue_exists(url, consumer_q), "consumer queue not declared"
        await asyncio.sleep(0.3)

        producer = RabbitMessageBus(
            url, queue_name=producer_q, routing_keys=["download.request"]
        )
        await producer.connect()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, task_id=str(task_id))
        try:
            await producer.publish(
                DownloadRequest(
                    task_id=task_id,
                    trace_id=trace_id,
                    source=SourceRef(url="https://example.com/x.mp4", kind="generic"),
                )
            )
        finally:
            structlog.contextvars.clear_contextvars()
        await producer.close()

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not seen:
            await asyncio.sleep(0.2)
        assert seen.get("trace_id") == trace_id, "consumer lost the trace_id"
        assert seen.get("task_id") == str(task_id), "consumer lost the task_id"
    finally:
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consume_task
        await consumer.close()
        await _purge_queues(url, consumer_q)


async def _purge_queues(url: str, consumer_q: str) -> None:
    """Best-effort: убрать тестовые очереди (DLX остаётся — общий)."""
    from aio_pika.exceptions import AMQPChannelError

    try:
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            for name in (consumer_q, f"{consumer_q}.dlq"):
                try:
                    await channel.queue_delete(name)
                except AMQPChannelError:
                    pass  # NOT_FOUND: уже убрана
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Критерий 3 (единый JSON-лог): stdlib-логгеры → тот же JSON, что structlog.
# ---------------------------------------------------------------------------


def test_stdlib_loggers_emit_parseable_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    structlog.contextvars.clear_contextvars()

    logging.getLogger("aio_pika.robust").info("probe from stdlib aio_pika")
    logging.getLogger("openai._base_client").info(
        "HTTP Request: POST https://api.openai.com/v1/chat/completions [started]"
    )
    structlog.contextvars.bind_contextvars(
        trace_id="t-stdlib", task_id="task-stdlib", stage="downloading"
    )
    try:
        logging.getLogger("aio_pika.robust").warning("connection lost, reconnecting")
        structlog.get_logger("swot.unit").info("structlog side", stage="downloading")
    finally:
        structlog.contextvars.clear_contextvars()

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 4, f"expected 4 log lines, got: {lines!r}"
    records = [json.loads(line) for line in lines]  # every line must be valid JSON

    assert records[0]["logger"] == "aio_pika.robust"
    assert records[0]["event"] == "probe from stdlib aio_pika"
    assert records[0]["level"] == "info"
    assert "timestamp" in records[0]
    assert records[1]["logger"] == "openai._base_client"
    assert records[2]["trace_id"] == "t-stdlib"
    assert records[2]["task_id"] == "task-stdlib"
    assert records[2]["stage"] == "downloading"
    assert records[3]["event"] == "structlog side"
    assert records[3]["trace_id"] == "t-stdlib"


# ---------------------------------------------------------------------------
# Критерий 3 (бот): handle_link биндит контекст ДО первого publish.
# ---------------------------------------------------------------------------

ADMIN_ID = 1001


class _RecordingBus:
    """MessageBus-стуб: записывает сообщения и контекст на момент publish."""

    def __init__(self) -> None:
        self.published: list[BaseMessage] = []
        self.ctx_at_publish: list[dict[str, object]] = []

    async def publish(self, message: BaseMessage) -> None:
        self.published.append(message)
        self.ctx_at_publish.append(structlog.contextvars.get_contextvars())

    async def consume(self, handler: object) -> None:
        pass


def _make_bot_container(bus: _RecordingBus, registry: InMemoryJobRegistry) -> object:
    from dishka import Provider, Scope, make_async_container, provide
    from swot_bot.validation import UrlValidator

    class _Provider(Provider):
        def __init__(self, recording_bus: _RecordingBus, reg: InMemoryJobRegistry):
            super().__init__()
            self._bus = recording_bus
            self._registry = reg

        @provide(scope=Scope.APP)
        def bus(self) -> MessageBus:
            return self._bus

        @provide(scope=Scope.APP)
        def registry(self) -> JobRegistry:
            return self._registry

        @provide(scope=Scope.APP)
        def validator(self) -> UrlValidator:
            return UrlValidator()

    return make_async_container(_Provider(bus, registry))


async def test_handle_link_binds_context_before_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.types import Chat, Message, Update, User
    from dishka.integrations.aiogram import inject, inject_router, setup_dishka
    from swot_bot.handlers import build_router

    structlog.contextvars.clear_contextvars()
    bus = _RecordingBus()
    registry = InMemoryJobRegistry()
    container = _make_bot_container(bus, registry)

    answers: list[str] = []

    async def fake_answer(
        self: object, text: str | None = None, **kwargs: object
    ) -> object:
        answers.append(str(text))
        return None

    monkeypatch.setattr(Message, "answer", fake_answer)

    router, _ = build_router(admin_id=ADMIN_ID)
    dp = Dispatcher()
    dp.include_router(router)
    setup_dishka(container=container, router=dp, auto_inject=True)
    inject_router(dp, inject)  # в проде это делает dp.start_polling() через startup

    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=ADMIN_ID, type="private"),
            from_user=User(id=ADMIN_ID, is_bot=False, first_name="admin"),
            text="https://example.com/lecture.mp4",
        ),
    )
    bot = Bot(token="123456:TEST")

    async with container():
        await dp.feed_update(bot, update)
    await container.close()

    assert len(bus.published) == 1, (
        "handle_link must publish exactly one DownloadRequest"
    )
    request = bus.published[0]
    assert isinstance(request, DownloadRequest)
    assert request.source.url == "https://example.com/lecture.mp4"

    ctx = bus.ctx_at_publish[0]
    assert ctx.get("trace_id") == request.trace_id, (
        "trace_id must be bound before the first publish"
    )
    assert ctx.get("task_id") == str(request.task_id), (
        "task_id must be bound before the first publish"
    )
    assert ctx.get("stage") == "new"

    # Контекст не должен протекать за пределы обработчика апдейта.
    assert structlog.contextvars.get_contextvars().get("trace_id") is None
    assert any("✅" in a for a in answers)
