"""End-to-end pipeline test: bot -> downloader -> transcriber -> analyzer.

Uses FakeBus as the transport with a tiny type-based router so each published
event is synchronously handed to the next stage, mimicking RabbitMQ bindings.
"""

from pathlib import Path
from uuid import uuid4

from fakes import FakeBus
from stubs import (
    LocalPrompt,
    StubExtractor,
    StubRouter,
    StubSummarizer,
    StubTranscriber,
)
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
from swot_downloader.service import DownloaderService
from swot_transcriber.service import TranscribeService


async def test_full_pipeline_end_to_end(tmp_path: Path) -> None:
    bus = FakeBus(capture=True)
    registry = InMemoryJobRegistry()
    media_dir = tmp_path / "media"
    artifacts_dir = tmp_path / "artifacts"
    media_dir.mkdir()
    artifacts_dir.mkdir()

    downloader = DownloaderService(
        media_dir=str(media_dir),
        router=StubRouter(
            tmp_path,
            filename="lecture.m4a",
            payload=b"fake-video",
            title="Квантовая механика: основы",
            duration_sec=120,
        ),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        artifacts_dir=str(artifacts_dir),
        retention_hours=168,
    )
    transcriber = TranscribeService(
        artifacts_dir=str(artifacts_dir),
        extractor=StubExtractor(),
        transcriber=StubTranscriber(text="Введение", end=10),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        media_dir=str(media_dir),
    )
    analyzer = AnalyzeService(
        prompt_name="lecture-summary",
        prompt_provider=LocalPrompt(),  # type: ignore[arg-type]
        summarizer=StubSummarizer(
            Summary(
                summary="Кратко",
                sections=[
                    Section(
                        heading="Введение", facts=[Fact(text="Факт №1", start_sec=0)]
                    )
                ],
            )
        ),  # type: ignore[arg-type]
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
