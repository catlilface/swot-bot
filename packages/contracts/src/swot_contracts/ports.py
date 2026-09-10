"""Port interfaces (Protocols) for infrastructure — swap adapters freely.

Kept in contracts so services depend only on these, never on concrete
adapters (SolID "D"). Implementations live in each service's composition root
or in the shared ``bus`` package.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from .events import BaseMessage


class MessageBus(Protocol):
    """Publish/consume pipeline events on the broker."""

    async def publish(self, message: BaseMessage) -> None: ...

    async def consume(
        self, handler: Callable[[BaseMessage], Awaitable[None]]
    ) -> None: ...

    async def is_ready(self) -> bool: ...

    async def close(self) -> None: ...


class JobStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"


class Job(Protocol):
    """A tracked pipeline run for one submitted link."""

    task_id: UUID
    status: JobStatus
    source_url: str
    message_id: int | None


class JobRegistry(Protocol):
    """Track task lifecycle (idempotency + status). In-memory by default.

    ``stuck_tasks``/``purge_final`` support the reaper (P1-5): tasks stuck in a
    non-final status past TTL are failed with a timeout, and final entries are
    forgotten after TTL so the registry does not grow unbounded.
    """

    async def create(self, task_id: UUID, source_url: str) -> None: ...
    async def exists(self, task_id: UUID) -> bool: ...
    async def set_status(self, task_id: UUID, status: JobStatus) -> None: ...
    async def get_status(self, task_id: UUID) -> JobStatus | None: ...
    async def stuck_tasks(self, ttl_sec: float) -> list[tuple[UUID, JobStatus]]: ...
    async def purge_final(self, ttl_sec: float) -> int: ...


__all__ = ["Job", "JobRegistry", "JobStatus", "MessageBus"]
