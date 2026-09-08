"""Unit tests for the analyzer service (StubSummarizer + FakeBus)."""

from json import load
from pathlib import Path
from uuid import UUID

from swot_analyzer.domain import Fact, Section, Summary
from swot_analyzer.service import AnalyzeService
from swot_bus import FakeBus, InMemoryJobRegistry
from swot_contracts import JobStatus, SourceRef, TranscriptReady


class StubSummarizer:
    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return Summary(
            title="Лекция",
            summary="Резюме",
            sections=[Section(heading="Гл1", facts=[Fact("Факт из транскрипта", 5, 12)])],
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
    assert bus.published_types()[0].value == "analysis.ready"
    summary = load((base_dir / "summary.json").open(encoding="utf-8"))
    assert summary["title"] == "Лекция"
    assert summary["sections"][0]["facts"][0]["start_sec"] == 5
    assert await registry.get_status(task_id) == JobStatus.READY
