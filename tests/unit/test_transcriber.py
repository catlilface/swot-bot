"""Unit tests for the transcriber service (FakeTranscriber + FakeBus)."""

from pathlib import Path
from uuid import UUID

from swot_bus import FakeBus, InMemoryJobRegistry
from swot_contracts import JobStatus, SourceRef, VideoDownloaded
from swot_transcriber.domain import TranscriptResult
from swot_transcriber.service import TranscribeService


class StubExtractor:
    async def extract(self, media_path: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"pcm")
        return dst


class StubTranscriber:
    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        srt = out_dir / "transcript.srt"
        segs = out_dir / "segments.json"
        srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nТест.\n", encoding="utf-8")
        segs.write_text('[{"start": 0, "end": 2, "text": "Тест."}]', encoding="utf-8")
        return TranscriptResult(
            base_dir=out_dir, srt_path=srt, segments_path=segs, language="ru"
        )


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
    assert await registry.get_status(task_id) == JobStatus.READY
    # media удаляется после успешной транскрипции
    assert not (tmp_path / "m.m4a").exists()
