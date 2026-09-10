"""Unit tests for the transcriber service (stub transcriber + FakeBus)."""

from pathlib import Path
from uuid import UUID

from fakes import FakeBus
from stubs import StubExtractor, StubTranscriber
from swot_bus import InMemoryJobRegistry
from swot_contracts import JobProgress, JobStatus, SourceRef, VideoDownloaded
from swot_transcriber.service import TranscribeService


async def test_transcribe_publishes_transcript_ready(tmp_path: Path) -> None:
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    task_id = UUID("00000000-0000-0000-0000-000000000002")
    svc = TranscribeService(
        artifacts_dir=str(tmp_path),
        extractor=StubExtractor(),
        transcriber=StubTranscriber(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        media_dir=str(tmp_path),
    )
    # media должен существовать до транскрипции
    (tmp_path / "m.m4a").write_bytes(b"fake-media")
    await svc.handle(
        VideoDownloaded(
            task_id=task_id,
            trace_id="t-2",
            source=SourceRef(url="https://disk.yandex.ru/i/abc"),
            media_path=str(tmp_path / "m.m4a"),
            title="test",
            duration_sec=2,
        )
    )
    assert "transcript.ready" in [t.value for t in bus.published_types()]
    ready = bus.get(1)
    assert ready.msg_type.value == "transcript.ready"
    assert ready.srt_path.endswith("transcript.srt")
    assert ready.language == "ru"
    # T-2.4: заголовок из video.downloaded переносится в transcript.ready
    assert ready.title == "test"
    # P2-1: READY — только analyzer; после расшифровки — промежуточный статус.
    assert await registry.get_status(task_id) == JobStatus.TRANSCRIBED


async def test_transcriber_publishes_job_progress(tmp_path: Path) -> None:
    """T-1.6: до транскрипции публикуется JobProgress(stage="transcribing")."""
    bus = FakeBus()
    task_id = UUID("00000000-0000-0000-0000-000000000012")
    (tmp_path / "m.m4a").write_bytes(b"fake-media")
    svc = TranscribeService(
        artifacts_dir=str(tmp_path),
        extractor=StubExtractor(),
        transcriber=StubTranscriber(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=InMemoryJobRegistry(),  # type: ignore[arg-type]
        media_dir=str(tmp_path),
    )
    await svc.handle(
        VideoDownloaded(
            task_id=task_id,
            trace_id="t-12",
            source=SourceRef(url="https://disk.yandex.ru/i/abc"),
            media_path=str(tmp_path / "m.m4a"),
            title="test",
            duration_sec=2,
        )
    )
    progress = bus.get(0)
    assert isinstance(progress, JobProgress)
    assert progress.msg_type.value == "job.progress"
    assert progress.stage == "transcribing"
    assert progress.task_id == task_id
    assert progress.trace_id == "t-12"
    # media удаляется после успешной транскрипции
    assert not (tmp_path / "m.m4a").exists()
