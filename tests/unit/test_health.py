"""T-1.6: Health readiness и graceful shutdown.

Критерии:
1. До ``connect()`` → ``/readyz`` → ``{"status": "degraded"}`` (503); после —
   ``ok``. (юнит: readiness-проба; live: ``RabbitMessageBus.is_ready``.)
2. ``SIGTERM`` в процессе consume → процесс завершается кодом 0 за grace
   period, in-flight сообщение drain'ится (ack), очередь пуста — live-тест на
   RabbitMessageBus + subprocess-тест против настоящего entrypoint.
3. Compose: ``docker compose stop bot`` → контейнер уходит без ``Killed`` —
   проверяется subprocess-тестом (тот же механизм: SIGTERM → exit 0);
   compose-проверка прогоняется вручную поверх.
"""

import asyncio
import contextlib
import os
import signal
import socket
import sys
import time
from uuid import uuid4

import aio_pika
import aiohttp
import pytest
from swot_bus.rabbit import RabbitMessageBus
from swot_contracts import BaseMessage, DownloadRequest, SourceRef
from swot_observability import HealthServer, run_until_shutdown

# ---------------------------------------------------------------------------
# Критерий 1: /readyz reflects bus connectivity (readiness-проба).
# ---------------------------------------------------------------------------


async def _get(url: str) -> aiohttp.ClientResponse:
    async with aiohttp.ClientSession() as session:
        return await session.get(url)


async def test_readyz_reflects_readiness_and_healthz_stop() -> None:
    """readiness False → 503 degraded; True → 200 ok; stop() → порт закрыт."""
    ready = asyncio.Event()

    async def _readiness() -> bool:
        return ready.is_set()

    server = HealthServer(port=0, readiness=_readiness)
    await server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        # /healthz — liveness, always ok while the process runs.
        resp = await _get(f"{base}/healthz")
        assert resp.status == 200
        assert await resp.json() == {"status": "ok"}

        # readiness ещё False → degraded.
        resp = await _get(f"{base}/readyz")
        assert resp.status == 503
        assert await resp.json() == {"status": "degraded"}

        ready.set()
        resp = await _get(f"{base}/readyz")
        assert resp.status == 200
        assert await resp.json() == {"status": "ok"}
    finally:
        await server.stop()

    # После stop() порт закрыт (graceful: без зависших соединений).
    with pytest.raises(aiohttp.ClientConnectionError):
        await _get(f"{base}/healthz")


# ---------------------------------------------------------------------------
# Критерий 1 (live broker): RabbitMessageBus.is_ready = «bus подключён».
# ---------------------------------------------------------------------------

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


async def test_rabbit_bus_is_ready_live() -> None:
    """is_ready: False до connect(), True после, False после close()."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    queue = f"t16-probe-{uuid4().hex[:8]}"
    bus = RabbitMessageBus(url, queue_name=queue, routing_keys=[queue])
    assert await bus.is_ready() is False
    await bus.connect()
    try:
        assert await bus.is_ready() is True
    finally:
        await bus.close()
    assert await bus.is_ready() is False


# ---------------------------------------------------------------------------
# run_until_shutdown: оркестрация shutdown без брокера.
# ---------------------------------------------------------------------------


async def test_run_until_shutdown_stops_and_cancels_work() -> None:
    """Сигнал → все work-таски отменяются, функция возвращает None (exit 0)."""
    stop = asyncio.Event()
    started = asyncio.Event()

    async def work() -> None:
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(run_until_shutdown(work(), stop=stop))
    await started.wait()  # work реально пошёл (не «мгновенный» return)
    stop.set()
    await asyncio.wait_for(task, timeout=5)  # нормальный возврат → exit 0


async def test_run_until_shutdown_reraises_work_error() -> None:
    """Креш work (до сигнала) → исключение пробрасывается (exit != 0)."""
    stop = asyncio.Event()
    stopped = asyncio.Event()

    async def crashed() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    async def sibling() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()

    with pytest.raises(RuntimeError, match="boom"):
        await run_until_shutdown(crashed(), sibling(), stop=stop)
    assert stopped.is_set(), "sibling must be cancelled when work crashes"


# ---------------------------------------------------------------------------
# Критерий 2 (live): in-flight сообщение drain'ится при close(), ack, не теряется.
# ---------------------------------------------------------------------------


async def _queue_exists(url: str, name: str) -> bool:
    try:
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            await channel.declare_queue(name, passive=True)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _purge_queues(url: str, queue: str) -> None:
    from aio_pika.exceptions import AMQPChannelError

    try:
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            for name in (queue, f"{queue}.dlq"):
                with contextlib.suppress(AMQPChannelError):
                    await channel.queue_delete(name)
    except Exception:  # noqa: BLE001
        pass


async def test_inflight_drained_on_close_live() -> None:
    """close() дождётся in-flight handler → ack; в очереди ничего не остаётся."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    queue = f"t16-drain-{uuid4().hex[:8]}"
    bus = RabbitMessageBus(url, queue_name=queue, routing_keys=["download.request"])
    await bus.connect()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def handler(message: BaseMessage) -> None:
        started.set()
        await asyncio.sleep(1.0)  # in-flight во время close()
        finished.set()

    consume_task = asyncio.create_task(bus.consume(handler))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not await _queue_exists(url, queue):
            await asyncio.sleep(0.2)
        assert await _queue_exists(url, queue), "queue must be declared"

        await bus.publish(
            DownloadRequest(
                task_id=uuid4(),
                trace_id="t16-drain",
                source=SourceRef(url="https://example.com/x.mp4", kind="generic"),
            )
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not started.is_set():
            await asyncio.sleep(0.05)
        assert started.is_set(), "handler must start before close()"
        assert not finished.is_set(), "handler must still be in-flight"

        # Пропроуд-порядок shutdown: сначала stop consume, потом close bus
        # (close дожидается in-flight dispatch-тасок).
        consume_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consume_task
        await bus.close()  # drain: ждать in-flight, потом закрыть соединение

        assert finished.is_set(), (
            "in-flight handler must be drained (and thus ack'd) before close()"
        )
        async with await aio_pika.connect_robust(url) as probe:
            channel = await probe.channel()
            existing = await channel.declare_queue(queue, passive=True)
            leftover = await existing.get(no_ack=True, fail=False, timeout=1)
            assert leftover is None, "queue must be empty: message ack'd, not lost"
    finally:
        if not consume_task.done():
            consume_task.cancel()
        with contextlib.suppress(BaseException):
            await consume_task
        await _purge_queues(url, queue)


# ---------------------------------------------------------------------------
# Критерий 2+3: SIGTERM реальному entrypoint (subprocess) → exit 0, drain.
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_worker_sigterm_exits_zero_live() -> None:
    """SIGTERM `python -m swot_downloader` → /readyz ок → exit 0 за grace period."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    health_port = _free_port()
    env = {
        **os.environ,
        "BROKER__HOST": "127.0.0.1",
        "HEALTH_PORT": str(health_port),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "swot_downloader",
        env=env,
        cwd=os.getcwd(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    readyz = f"http://127.0.0.1:{health_port}/readyz"
    try:
        # Ждём, пока сервис подключится к брокеру (readiness → ok).
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                out = await proc.stdout.read()
                pytest.fail(
                    f"downloader exited early (code={proc.returncode}): "
                    f"{out.decode(errors='replace')[-2000:]}"
                )
            try:
                resp = await _get(readyz)
                if resp.status == 200:
                    break
            except aiohttp.ClientConnectionError:
                pass
            await asyncio.sleep(0.5)
        else:
            proc.kill()
            out = await proc.stdout.read()
            pytest.fail(
                f"downloader never became ready: {out.decode(errors='replace')[-2000:]}"
            )

        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except TimeoutError:
            proc.kill()
            out = await proc.stdout.read()
            pytest.fail(
                f"SIGTERM did not stop the worker in 30s: "
                f"{out.decode(errors='replace')[-2000:]}"
            )
        assert proc.returncode == 0, (
            f"SIGTERM must produce a clean exit 0, got {proc.returncode}"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


# ---------------------------------------------------------------------------
# Compose-критерий (3): SIGTERM-поведение бота — subprocess против
# swot_bot + fake-tg как Bot API (как в compose-стеке).
# ---------------------------------------------------------------------------


async def _wait_ready(url: str, timeout: float = 60.0) -> str | None:
    """Ждём 200 с ``url``; None — таймаут."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = await _get(url)
            if resp.status == 200:
                return None
        except aiohttp.ClientConnectionError:
            pass
        await asyncio.sleep(0.5)
    return f"never became ready: {url}"


async def test_bot_sigterm_exits_zero_live() -> None:
    """SIGTERM `python -m swot_bot` (polling+consumer) → exit 0 без «Killed»."""
    url = _rabbit_url()
    if not await _broker_available(url):
        pytest.skip("no RabbitMQ broker available")

    health_port = _free_port()
    fake_port = _free_port()
    fake = await asyncio.create_subprocess_exec(
        sys.executable,
        "services/fake/fake_telegram.py",
        env={**os.environ, "FAKE_PORT": str(fake_port)},
        cwd=os.getcwd(),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "BROKER__HOST": "127.0.0.1",
        "HEALTH_PORT": str(health_port),
        "TELEGRAM__TOKEN": "123456:TEST-TOKEN",
        "TELEGRAM__API_BASE_URL": f"http://127.0.0.1:{fake_port}",
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "swot_bot",
        env=env,
        cwd=os.getcwd(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await _wait_ready(f"http://127.0.0.1:{fake_port}/healthz")
        error = await _wait_ready(
            f"http://127.0.0.1:{health_port}/readyz",
        )
        if error:
            proc.kill()
            out = await proc.stdout.read()
            pytest.fail(f"{error}: {out.decode(errors='replace')[-2000:]}")

        proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except TimeoutError:
            proc.kill()
            out = await proc.stdout.read()
            pytest.fail(
                f"SIGTERM did not stop the bot in 30s: "
                f"{out.decode(errors='replace')[-2000:]}"
            )
        assert proc.returncode == 0, (
            f"SIGTERM must produce a clean exit 0, got {proc.returncode}"
        )
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        fake.kill()
        await fake.wait()


# ---------------------------------------------------------------------------
# Smoke: subprocess-запуск модуля не требует uv (editable-инсталл в venv).
# ---------------------------------------------------------------------------


def test_worker_modules_importable() -> None:
    """Entrypoints импортируются (python -m <service> найдётся)."""
    import importlib

    for module in ("swot_downloader", "swot_transcriber", "swot_analyzer", "swot_bot"):
        importlib.import_module(module)
    # -m вызывает <pkg>.__main__
    for module in ("swot_downloader.__main__", "swot_bot.__main__"):
        importlib.import_module(module)
