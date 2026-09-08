"""In-memory JobRegistry — no Redis needed (single admin + single chat)."""

from uuid import UUID

from swot_contracts import JobStatus


class InMemoryJobRegistry:
    """Thread/async-safe per-process job tracker.

    A dict keyed by task_id → status. Persists only for the process lifetime;
    sufficient for one admin + one target chat (see docs/architecture.md).
    """

    def __init__(self) -> None:
        self._jobs: dict[UUID, JobStatus] = {}
        self._urls: dict[UUID, str] = {}

    async def create(self, task_id: UUID, source_url: str) -> None:
        self._jobs[task_id] = JobStatus.PENDING
        self._urls[task_id] = source_url

    async def exists(self, task_id: UUID) -> bool:
        return task_id in self._jobs

    async def set_status(self, task_id: UUID, status: JobStatus) -> None:
        self._jobs[task_id] = status

    async def get_status(self, task_id: UUID) -> JobStatus | None:
        return self._jobs.get(task_id)
