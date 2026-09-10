"""Unit tests for the analyzer service (StubSummarizer + FakeBus)."""

from json import load
from pathlib import Path
from uuid import UUID

from fakes import FakeBus
from swot_analyzer.domain import Fact, Section, Summary
from swot_analyzer.service import AnalyzeService
from swot_bus import InMemoryJobRegistry
from swot_contracts import JobProgress, JobStatus, SourceRef, TranscriptReady


class StubSummarizer:
    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return Summary(
            summary="Резюме",
            sections=[
                Section(
                    heading="Гл1", facts=[Fact(text="Факт из транскрипта", start_sec=5)]
                )
            ],
        )


class LocalPrompt:
    def get(self, name: str) -> str:
        return "промпт"


async def test_analyzer_writes_summary_and_publishes(tmp_path: Path) -> None:
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    task_id = UUID("00000000-0000-0000-0000-000000000003")
    base_dir = tmp_path / "3"
    base_dir.mkdir(parents=True)
    (base_dir / "transcript.srt").write_text(
        "1\n00:00:05,000 --> 00:00:12,000\nФакт из транскрипта\n", encoding="utf-8"
    )
    svc = AnalyzeService(
        prompt_name="lecture-summary",
        prompt_provider=LocalPrompt(),  # type: ignore[arg-type]
        summarizer=StubSummarizer(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        artifacts_dir=str(tmp_path),
    )
    await svc.handle(
        TranscriptReady(
            task_id=task_id,
            trace_id="t-3",
            source=SourceRef(url="https://disk.yandex.ru/i/abc"),
            base_dir=str(base_dir),
            srt_path=str(base_dir / "transcript.srt"),
            segments_path=str(base_dir / "segments.json"),
        )
    )
    assert "analysis.ready" in [t.value for t in bus.published_types()]
    ready = bus.get(1)
    assert ready.msg_type.value == "analysis.ready"
    summary = load((base_dir / "summary.json").open(encoding="utf-8"))
    assert summary["sections"][0]["facts"][0]["start_sec"] == 5
    assert await registry.get_status(task_id) == JobStatus.READY


async def test_analyzer_publishes_job_progress(tmp_path: Path) -> None:
    """T-1.6: до анализа публикуется JobProgress(stage="analyzing")."""
    bus = FakeBus()
    task_id = UUID("00000000-0000-0000-0000-000000000013")
    base_dir = tmp_path / "13"
    base_dir.mkdir(parents=True)
    (base_dir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nТест\n", encoding="utf-8"
    )
    svc = AnalyzeService(
        prompt_name="lecture-summary",
        prompt_provider=LocalPrompt(),  # type: ignore[arg-type]
        summarizer=StubSummarizer(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=InMemoryJobRegistry(),  # type: ignore[arg-type]
        artifacts_dir=str(tmp_path),
    )
    await svc.handle(
        TranscriptReady(
            task_id=task_id,
            trace_id="t-13",
            source=SourceRef(url="https://disk.yandex.ru/i/abc"),
            base_dir=str(base_dir),
            srt_path=str(base_dir / "transcript.srt"),
            segments_path=str(base_dir / "segments.json"),
        )
    )
    progress = bus.get(0)
    assert isinstance(progress, JobProgress)
    assert progress.msg_type.value == "job.progress"
    assert progress.stage == "analyzing"
    assert progress.task_id == task_id
    assert progress.trace_id == "t-13"
