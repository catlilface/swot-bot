"""In-memory JobRegistry — no Redis needed (single admin + single chat)."""

import time
from collections.abc import Callable
from uuid import UUID

from swot_contracts import JobStatus

#: Statuses after which a task is terminal (never reaped / re-run).
FINAL_STATUSES = frozenset({JobStatus.READY, JobStatus.FAILED})


class InMemoryJobRegistry:
    """Thread/async-safe per-process job tracker.

    A dict keyed by task_id → status. Persists only for the process lifetime;
    sufficient for one admin + one target chat (see docs/architecture.md).

    Every entry carries a monotonic last-update timestamp so the reaper can
    find tasks stuck in a non-final status past TTL (P1-5) and TTL-cleanup
    can forget final entries (P1-11).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._jobs: dict[UUID, JobStatus] = {}
        self._updated_at: dict[UUID, float] = {}
        self._urls: dict[UUID, str] = {}

    async def create(self, task_id: UUID, source_url: str) -> None:
        self._jobs[task_id] = JobStatus.PENDING
        self._urls[task_id] = source_url
        self._touch(task_id)

    async def exists(self, task_id: UUID) -> bool:
        return task_id in self._jobs

    async def set_status(self, task_id: UUID, status: JobStatus) -> None:
        self._jobs[task_id] = status
        self._touch(task_id)

    async def get_status(self, task_id: UUID) -> JobStatus | None:
        return self._jobs.get(task_id)

    async def stuck_tasks(self, ttl_sec: float) -> list[tuple[UUID, JobStatus]]:
        """Tasks without a final status whose last update is older than ttl."""
        now = self._clock()
        stuck = []
        for task_id, status in self._jobs.items():
            if status in FINAL_STATUSES:
                continue
            if now - self._updated_at[task_id] > ttl_sec:
                stuck.append((task_id, status))
        return stuck

    async def purge_final(self, ttl_sec: float) -> int:
        """Drop final (READY/FAILED) entries untouched for longer than ttl.

        Returns the number of entries removed.
        """
        now = self._clock()
        expired = [
            task_id
            for task_id, status in self._jobs.items()
            if status in FINAL_STATUSES and now - self._updated_at[task_id] > ttl_sec
        ]
        for task_id in expired:
            self._jobs.pop(task_id, None)
            self._updated_at.pop(task_id, None)
            self._urls.pop(task_id, None)
        return len(expired)

    def _touch(self, task_id: UUID) -> None:
        self._updated_at[task_id] = self._clock()
