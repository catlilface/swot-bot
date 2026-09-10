"""End-to-end pipeline test: bot -> downloader -> transcriber -> analyzer.

Uses FakeBus as the transport with a tiny type-based router so each published
event is synchronously handed to the next stage, mimicking RabbitMQ bindings.
"""

from pathlib import Path
from uuid import uuid4

from fakes import FakeBus
from swot_analyzer.domain import Fact, Section, Summary
from swot_analyzer.service import AnalyzeService
from swot_bot.renderer import MessageRenderer
from swot_bus import InMemoryJobRegistry
from swot_contracts import (
    AnalysisReady,
    BaseMessage,
    DownloadRequest,
    SourceRef,
    TranscriptReady,
    VideoDownloaded,
)
from swot_downloader.domain import DownloadedMedia
from swot_downloader.service import DownloaderService
from swot_transcriber.domain import TranscriptResult
from swot_transcriber.service import TranscribeService


class StubRouter:
    """Downloader adapter stub: writes a fake media file."""

    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        dst_dir.mkdir(parents=True, exist_ok=True)
        f = dst_dir / "lecture.m4a"
        f.write_bytes(b"fake-video")
        return DownloadedMedia(
            media_path=f, title="Квантовая механика: основы", duration_sec=120,
            resource_id="r1",
        )


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
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:10,000\nВведение\n",
            encoding="utf-8",
        )
        segs.write_text(
            '[{"start": 0, "end": 10, "text": "Введение"}]', encoding="utf-8"
        )
        return TranscriptResult(
            base_dir=out_dir, srt_path=srt, segments_path=segs, language="ru"
        )


class StubSummarizer:
    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return Summary(
            summary="Кратко",
            sections=[
                Section(heading="Введение", facts=[Fact(text="Факт №1", start_sec=0)])
            ],
        )


class LocalPrompt:
    def get(self, name: str) -> str:
        return "промпт"


async def test_full_pipeline_end_to_end(tmp_path: Path) -> None:
    bus = FakeBus(capture=True)
    registry = InMemoryJobRegistry()
    media_dir = tmp_path / "media"
    artifacts_dir = tmp_path / "artifacts"
    media_dir.mkdir()
    artifacts_dir.mkdir()

    downloader = DownloaderService(
        media_dir=str(media_dir),
        router=StubRouter(tmp_path),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        artifacts_dir=str(artifacts_dir),
        retention_hours=168,
    )
    transcriber = TranscribeService(
        artifacts_dir=str(artifacts_dir),
        extractor=StubExtractor(),
        transcriber=StubTranscriber(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        media_dir=str(media_dir),
    )
    analyzer = AnalyzeService(
        prompt_name="lecture-summary",
        prompt_provider=LocalPrompt(),  # type: ignore[arg-type]
        summarizer=StubSummarizer(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        artifacts_dir=str(artifacts_dir),
    )

    async def router(message: BaseMessage) -> None:
        if isinstance(message, DownloadRequest):
            await downloader.handle(message)
        elif isinstance(message, VideoDownloaded):
            await transcriber.handle(message)
        elif isinstance(message, TranscriptReady):
            await analyzer.handle(message)

    await bus.consume(router)

    await bus.publish(
        DownloadRequest(
            task_id=uuid4(),
            trace_id="t-e2e",
            source=SourceRef(url="https://disk.yandex.ru/i/abc", kind="yandex_disk"),
        )
    )

    ready = next((m for m in bus.published if isinstance(m, AnalysisReady)), None)
    assert ready is not None, [t.value for t in bus.published_types()]
    # T-2.4: ивент несёт путь к SRT — файл реально лежит на томе
    assert Path(ready.srt_path).exists()
    summary_path = Path(ready.summary_path)
    assert summary_path.exists()
    text = MessageRenderer().render(summary_path)
    # T-2.4: заголовок из download (title в stub-роутере) попал в сообщение
    assert "Квантовая механика: основы" in text
    assert "Факт №1" in text
    assert "00:00" in text  # факт на 0 секунд -> MM:SS
