"""Reaper for stuck pipeline tasks (P1-5).

A task whose status has not advanced to a final state (READY/FAILED) within
the TTL window is considered stuck (crashed worker, lost connection, ...).
The reaper publishes ``JobFailed(stage, error="timeout: ...")`` for each stuck
task and marks it FAILED so the bot notifies the admin.
"""

import asyncio
import logging
from uuid import UUID

from swot_contracts import JobFailed, JobStatus, MessageBus
from swot_contracts.ports import JobRegistry

logger = logging.getLogger(__name__)

#: JobFailed.stage naming follows the existing stage conventions
#: (download / transcribe / analyze). A task stuck at a completed stage's
#: status is timed out in the *next* stage (P2-1).
_STAGE_BY_STATUS: dict[JobStatus, str] = {
    JobStatus.PENDING: "download",
    JobStatus.DOWNLOADING: "download",
    JobStatus.DOWNLOADED: "transcribe",
    JobStatus.TRANSCRIBING: "transcribe",
    JobStatus.TRANSCRIBED: "analyze",
    JobStatus.ANALYZING: "analyze",
}


def _trace_id_for(task_id: UUID) -> str:
    """Reconstruct the pipeline trace id (same formula as the bot)."""
    return f"t-{task_id.hex[:12]}"


class JobReaper:
    """Periodically fail tasks stuck in a non-final status past TTL."""

    def __init__(
        self,
        registry: JobRegistry,
        bus: MessageBus,
        ttl_sec: float,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._ttl_sec = ttl_sec

    async def reap_once(self) -> int:
        """Fail all stuck tasks; returns the number of tasks reaped."""
        reaped = 0
        for task_id, status in await self._registry.stuck_tasks(self._ttl_sec):
            stage = _STAGE_BY_STATUS.get(status, status.value)
            error = (
                f"timeout: stuck in {status.value} for more than {self._ttl_sec:.0f}s"
            )
            try:
                await self._bus.publish(
                    JobFailed(
                        task_id=task_id,
                        trace_id=_trace_id_for(task_id),
                        stage=stage,
                        error=error,
                    )
                )
                await self._registry.set_status(task_id, JobStatus.FAILED)
                reaped += 1
                logger.warning(
                    "reaped stuck task: task_id=%s stage=%s status=%s",
                    task_id,
                    stage,
                    status.value,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to reap task: task_id=%s",
                    task_id,
                )
        return reaped

    async def run(self, interval_sec: float = 60.0) -> None:
        """Long-running reaper loop (cancelling the task stops it)."""
        while True:
            await asyncio.sleep(interval_sec)
            try:
                await self.reap_once()
            except Exception:  # noqa: BLE001
                logger.exception("reaper iteration failed")
