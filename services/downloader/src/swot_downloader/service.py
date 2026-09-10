"""Downloader application service: consumes download.request, downloads, publishes.

Also owns the periodic maintenance (P1-5/P1-11): the reaper fails tasks stuck
in a non-final status past TTL, and TTL-cleanup purges final registry entries
plus stale task directories in both ``media_dir`` and ``artifacts_dir``.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from swot_contracts import (
    BaseMessage,
    DownloadRequest,
    JobProgress,
    JobStatus,
    MessageBus,
    VideoDownloaded,
)
from swot_contracts.ports import JobRegistry

from .cleanup import purge_stale_dirs
from .domain import DownloadedMedia
from .reaper import JobReaper

if TYPE_CHECKING:
    from .adapters import SourceRouter

logger = logging.getLogger(__name__)


#: How often the reaper scans for stuck tasks (seconds).
REAPER_INTERVAL_SEC = 60.0
#: How often registry/directories are TTL-cleaned (seconds).
CLEANUP_INTERVAL_SEC = 3600.0


class DownloaderService:
    """Handle a download.request event end-to-end."""

    def __init__(
        self,
        media_dir: str,
        router: "SourceRouter",
        bus: MessageBus,
        registry: JobRegistry,
        max_duration_sec: int = 7200,
        artifacts_dir: str | None = None,
        retention_hours: int = 168,
        job_timeout_hours: float = 6.0,
    ) -> None:
        self._media_dir = Path(media_dir)
        self._router = router
        self._bus = bus
        self._registry = registry
        self._max_duration_sec = max_duration_sec
        self._artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self._retention_hours = retention_hours
        self._job_timeout_sec = job_timeout_hours * 3600

    async def handle(self, message: DownloadRequest) -> None:
        await self._registry.set_status(message.task_id, JobStatus.DOWNLOADING)
        await self._bus.publish(
            JobProgress(
                task_id=message.task_id, trace_id=message.trace_id, stage="downloading"
            )
        )
        dst = self._media_dir / str(message.task_id)
        try:
            media: DownloadedMedia = await self._router.download(message.source, dst)
            if media.duration_sec and media.duration_sec > self._max_duration_sec:
                raise ValueError(f"video too long: {media.duration_sec}s")
            await self._bus.publish(
                VideoDownloaded(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    source=message.source,
                    media_path=str(media.media_path),
                    title=media.title,
                    duration_sec=media.duration_sec,
                    resource_id=media.resource_id,
                )
            )
            # P2-1: READY means "result ready to deliver" — only the analyzer
            # sets it. Download done: the task now waits for transcription.
            await self._registry.set_status(message.task_id, JobStatus.DOWNLOADED)
            logger.info(
                "video downloaded: task_id=%s trace_id=%s path=%s title=%s",
                message.task_id,
                message.trace_id,
                media.media_path,
                media.title,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "download failed: task_id=%s trace_id=%s",
                message.task_id,
                message.trace_id,
            )
            from swot_contracts import JobFailed

            await self._bus.publish(
                JobFailed(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    stage="download",
                    error=str(exc),
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.FAILED)

    def cleanup_old_dirs(self) -> int:
        """TTL-cleanup: stale task dirs in media_dir and artifacts_dir (P1-11)."""
        cutoff = time.time() - self._retention_hours * 3600
        removed = 0
        for root in (self._media_dir, self._artifacts_dir):
            if root is None:
                continue
            removed += purge_stale_dirs(root, cutoff)
        return removed

    def make_reaper(self) -> JobReaper:
        """Build the stuck-task reaper with the configured TTL (P1-5)."""
        return JobReaper(self._registry, self._bus, self._job_timeout_sec)

    async def run(self) -> None:
        reaper = self.make_reaper()
        reaper_task = asyncio.create_task(reaper.run(REAPER_INTERVAL_SEC))
        cleanup_task = asyncio.create_task(self._cleanup_loop())

        async def dispatch(message: BaseMessage) -> None:
            if isinstance(message, DownloadRequest):
                await self.handle(message)

        try:
            await self._bus.consume(dispatch)
        finally:
            reaper_task.cancel()
            cleanup_task.cancel()
            await asyncio.gather(reaper_task, cleanup_task, return_exceptions=True)

    async def _cleanup_loop(self) -> None:
        """Periodic TTL cleanup: registry finals + media/artifacts dirs."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SEC)
            try:
                removed = self.cleanup_old_dirs()
                purged = await self._registry.purge_final(self._job_timeout_sec)
                if removed or purged:
                    logger.info(
                        "ttl cleanup: dirs_removed=%d registry_purged=%d",
                        removed,
                        purged,
                    )
            except Exception:  # noqa: BLE001
                logger.exception("ttl cleanup iteration failed")
