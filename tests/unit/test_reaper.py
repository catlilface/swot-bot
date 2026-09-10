"""Reaper + registry TTL behavior (P1-5/P1-11)."""

from uuid import UUID

from swot_bus import FakeBus, InMemoryJobRegistry
from swot_contracts import JobFailed, JobStatus
from swot_downloader.reaper import JobReaper

TTL = 3600.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += sec


def _task(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


async def test_reaper_fails_stuck_downloading_task() -> None:
    """Задача, застрявшая в DOWNLOADING дольше TTL → JobFailed со стадией и причиной."""
    clock = FakeClock()
    registry = InMemoryJobRegistry(clock=clock)
    bus = FakeBus()
    reaper = JobReaper(registry, bus, ttl_sec=TTL)

    task_id = _task(1)
    await registry.create(task_id, "https://example.com/v.mp4")
    await registry.set_status(task_id, JobStatus.DOWNLOADING)
    clock.advance(TTL + 1)

    reaped = await reaper.reap_once()

    assert reaped == 1
    failed = [m for m in bus.published if m.msg_type.value == "job.failed"]
    assert len(failed) == 1
    msg = failed[0]
    assert isinstance(msg, JobFailed)
    assert msg.task_id == task_id
    assert msg.stage == "download"
    assert "timeout" in msg.error
    assert "downloading" in msg.error
    # статус переведён в финальный
    assert await registry.get_status(task_id) == JobStatus.FAILED


async def test_reaper_ignores_fresh_tasks() -> None:
    clock = FakeClock()
    registry = InMemoryJobRegistry(clock=clock)
    bus = FakeBus()
    reaper = JobReaper(registry, bus, ttl_sec=TTL)

    fresh_task = _task(2)
    await registry.create(fresh_task, "https://example.com/v.mp4")
    await registry.set_status(fresh_task, JobStatus.DOWNLOADING)
    clock.advance(TTL - 10)  # ещё не истекло

    assert await reaper.reap_once() == 0
    assert bus.published == []
    assert await registry.get_status(fresh_task) == JobStatus.DOWNLOADING


async def test_reaper_ignores_final_statuses() -> None:
    clock = FakeClock()
    registry = InMemoryJobRegistry(clock=clock)
    bus = FakeBus()
    reaper = JobReaper(registry, bus, ttl_sec=TTL)

    ready_task = _task(3)
    failed_task = _task(4)
    await registry.create(ready_task, "https://example.com/a")
    await registry.set_status(ready_task, JobStatus.READY)
    await registry.create(failed_task, "https://example.com/b")
    await registry.set_status(failed_task, JobStatus.FAILED)
    clock.advance(TTL * 10)

    assert await reaper.reap_once() == 0
    assert bus.published == []


async def test_registry_purge_final_only() -> None:
    """TTL-очистка: финальные записи старше TTL удаляются; остальные — нет."""
    clock = FakeClock()
    registry = InMemoryJobRegistry(clock=clock)

    old_ready = _task(5)
    fresh_ready = _task(6)
    old_downloading = _task(7)
    await registry.create(old_ready, "u1")
    await registry.set_status(old_ready, JobStatus.READY)
    await registry.create(old_downloading, "u3")
    await registry.set_status(old_downloading, JobStatus.DOWNLOADING)
    # оба «старых» теперь старше TTL
    clock.advance(TTL + 1)
    await registry.create(fresh_ready, "u2")
    await registry.set_status(fresh_ready, JobStatus.READY)
    clock.advance(1)  # свежая запись всё ещё в пределах TTL
    purged = await registry.purge_final(TTL)

    assert purged == 1
    assert await registry.get_status(old_ready) is None
    assert await registry.get_status(fresh_ready) == JobStatus.READY
    # не-финальная запись никогда не чистится
    assert await registry.get_status(old_downloading) == JobStatus.DOWNLOADING


async def test_stuck_tasks_uses_last_update() -> None:
    """Обновление статуса сбрасывает TTL (reaper не убивает живые задачи)."""
    clock = FakeClock()
    registry = InMemoryJobRegistry(clock=clock)

    task_id = _task(8)
    await registry.create(task_id, "u")
    await registry.set_status(task_id, JobStatus.DOWNLOADING)
    clock.advance(TTL + 1)
    await registry.set_status(task_id, JobStatus.TRANSCRIBING)  # живой прогресс
    await registry.set_status(task_id, JobStatus.ANALYZING)

    assert await registry.stuck_tasks(TTL) == []


async def test_default_clock_is_monotonic() -> None:
    """По умолчанию используется time.monotonic (в проде чистить часы не нужно)."""
    registry = InMemoryJobRegistry()
    task_id = UUID(int=0)
    await registry.create(task_id, "u")
    # только что созданная задача не может быть «застрявшей» при честном TTL
    assert await registry.stuck_tasks(3600.0) == []
    assert await registry.get_status(task_id) == JobStatus.PENDING
