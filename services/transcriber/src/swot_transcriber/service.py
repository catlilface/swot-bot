"""Transcriber application service: consumes video.downloaded -> transcript.ready."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from swot_contracts import (
    JobStatus,
    MessageBus,
    VideoDownloaded,
)
from swot_contracts.ports import JobRegistry

from .domain import AudioExtractor

if TYPE_CHECKING:
    from .domain import Transcriber

logger = logging.getLogger(__name__)


class TranscribeService:
    """Handle video.downloaded end-to-end: extract audio, transcribe, publish."""

    def __init__(
        self,
        artifacts_dir: str,
        extractor: AudioExtractor,
        transcriber: "Transcriber",
        bus: MessageBus,
        registry: JobRegistry,
    ) -> None:
        self._artifacts_dir = Path(artifacts_dir)
        self._extractor = extractor
        self._transcriber = transcriber
        self._bus = bus
        self._registry = registry

    async def handle(self, message: VideoDownloaded) -> None:
        await self._registry.set_status(message.task_id, JobStatus.TRANSCRIBING)
        out_dir = self._artifacts_dir / str(message.task_id)
        try:
            work = out_dir / "work"
            work.mkdir(parents=True, exist_ok=True)
            audio = await self._extractor.extract(
                Path(message.media_path), work / "audio.wav"
            )
            result = await self._transcriber.transcribe(audio, out_dir)

            from swot_contracts import TranscriptReady

            await self._bus.publish(
                TranscriptReady(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    source=message.source,
                    base_dir=str(out_dir),
                    srt_path=str(result.srt_path),
                    segments_path=str(result.segments_path),
                    language=result.language,
                    duration_sec=message.duration_sec,
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.READY)
        except Exception as exc:  # noqa: BLE001
            logger.exception("transcribe failed", task_id=str(message.task_id))
            from swot_contracts import JobFailed

            await self._bus.publish(
                JobFailed(
                    task_id=message.task_id,
                    trace_id=message.trace_id,
                    stage="transcribe",
                    error=str(exc),
                )
            )
            await self._registry.set_status(message.task_id, JobStatus.FAILED)

    async def run(self) -> None:
        await self._bus.consume(self.handle)
