"""Downloader application service: consumes download.request, downloads, publishes."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from swot_contracts import (
    DownloadRequest,
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
    ) -> None:
        self._media_dir = Path(media_dir)
        self._router = router
        self._bus = bus
        self._registry = registry
        self._max_duration_sec = max_duration_sec

    async def handle(self, message: DownloadRequest) -> None:
        await self._registry.set_status(message.task_id, JobStatus.DOWNLOADING)
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

    async def run(self) -> None:
        await self._bus.consume(self.handle)
