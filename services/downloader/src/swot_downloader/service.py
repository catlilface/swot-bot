"""Downloader application service: consumes download.request, downloads, publishes."""

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from swot_contracts import (
    DownloadRequest,
    JobProgress,
    JobStatus,
    MessageBus,
    VideoDownloaded,
)
from swot_contracts.ports import JobRegistry

from .domain import DownloadedMedia

if TYPE_CHECKING:
    from .adapters import SourceRouter

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._media_dir = Path(media_dir)
        self._router = router
        self._bus = bus
        self._registry = registry
        self._max_duration_sec = max_duration_sec
        self._artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self._retention_hours = retention_hours

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
            await self._registry.set_status(message.task_id, JobStatus.READY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("download failed", task_id=str(message.task_id))
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

    async def _cleanup_old_artifacts(self, retention_hours: int) -> None:
        """Delete artifacts older than the retention window (best-effort)."""
        if self._artifacts_dir is None:
            return
        cutoff = time.time() - retention_hours * 3600
        for child in list(self._artifacts_dir.iterdir()):
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    for f in child.rglob("*"):
                        if f.is_file():
                            f.unlink(missing_ok=True)
                    child.rmdir()
                    logger.info("cleaned artifacts", path=str(child))
            except OSError as exc:
                logger.warning("cleanup failed", path=str(child), error=str(exc))

    async def run(self, cleanup_interval: int = 3600) -> None:
        async def _cleanup_loop() -> None:
            while True:
                await asyncio.sleep(cleanup_interval)
                await self._cleanup_old_artifacts(self._retention_hours)

        cleanup_task = (
            asyncio.create_task(_cleanup_loop()) if self._artifacts_dir else None
        )
        try:
            await self._bus.consume(self.handle)
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
