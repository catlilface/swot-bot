"""T-0.6: path containment (resolve_under) — helper + service-level regression."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from swot_analyzer.service import AnalyzeService
from swot_bot.handlers import ResultReporter
from swot_bus import FakeBus, InMemoryJobRegistry
from swot_contracts import (
    AnalysisReady,
    JobStatus,
    SourceRef,
    TranscriptReady,
    VideoDownloaded,
    resolve_under,
)
from swot_transcriber.service import TranscribeService

# --- helper ---------------------------------------------------------------


def test_resolve_under_relative_ok(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    resolved = resolve_under(base, "sub/file.txt")
    assert resolved == base / "sub" / "file.txt"


def test_resolve_under_absolute_inside_ok(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    target = base / "file.txt"
    resolved = resolve_under(base, target)
    assert resolved == target.resolve()


def test_resolve_under_base_itself_ok(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    assert resolve_under(base, base) == base.resolve()


def test_resolve_under_dotdot_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        resolve_under(base, "../etc/passwd")


def test_resolve_under_absolute_outside_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        resolve_under(base, "/etc/passwd")


def test_resolve_under_nested_dotdot_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(ValueError):
        resolve_under(base, "a/b/../../etc/passwd")


def test_resolve_under_symlink_escape_rejected(tmp_path: Path) -> None:
    base = tmp_path / "base"
    outside = tmp_path / "outside.txt"
    base.mkdir()
    outside.write_text("secret", encoding="utf-8")
    link = base / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_under(base, link)


def test_resolve_under_symlink_inside_ok(tmp_path: Path) -> None:
    base = tmp_path / "base"
    target = base / "real.txt"
    base.mkdir()
    target.write_text("ok", encoding="utf-8")
    (base / "link").symlink_to(target)
    assert resolve_under(base, base / "link") == target.resolve()


# --- analyzer -------------------------------------------------------------


class _StubPrompt:
    def get(self, name: str) -> str:
        return "prompt"


class _StubSummarizer:
    async def summarize(self, transcript: str, prompt: str) -> Any:
        from swot_analyzer.domain import Fact, Section, Summary

        return Summary(
            summary="Резюме",
            sections=[Section(heading="Гл1", facts=[Fact(text="Факт", start_sec=5)])],
        )


def _make_analyzer(
    tmp_path: Path, bus: FakeBus, registry: InMemoryJobRegistry
) -> AnalyzeService:
    return AnalyzeService(
        prompt_name="lecture-summary",
        prompt_provider=_StubPrompt(),  # type: ignore[arg-type]
        summarizer=_StubSummarizer(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        artifacts_dir=str(tmp_path / "artifacts"),
    )


@pytest.mark.asyncio
async def test_analyzer_rejects_base_dir_escape(tmp_path: Path) -> None:
    """base_dir='../../' → summary.json не создаётся вне artifacts, задача FAILED."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "t").mkdir()
    (tmp_path / "artifacts" / "t" / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nТекст\n", encoding="utf-8"
    )
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    svc = _make_analyzer(tmp_path, bus, registry)

    task_id = uuid4()
    await svc.handle(
        TranscriptReady(
            task_id=task_id,
            trace_id="t-esc",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir="../../",
            srt_path="t/transcript.srt",
            segments_path="t/segments.json",
        )
    )

    assert await registry.get_status(task_id) == JobStatus.FAILED
    assert "job.failed" in [t.value for t in bus.published_types()]
    # summary.json не появился нигде вне artifacts
    summaries = list(tmp_path.rglob("summary.json"))
    assert all(s.is_relative_to(tmp_path / "artifacts") for s in summaries)


@pytest.mark.asyncio
async def test_analyzer_rejects_srt_outside_base(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    base = tmp_path / "artifacts" / "t"
    base.mkdir()
    (base / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nТекст\n", encoding="utf-8"
    )
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    svc = _make_analyzer(tmp_path, bus, registry)

    task_id = uuid4()
    await svc.handle(
        TranscriptReady(
            task_id=task_id,
            trace_id="t-esc2",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir=str(base),
            srt_path=str(tmp_path / "artifacts" / "other.srt"),  # вне base_dir
            segments_path=str(base / "segments.json"),
        )
    )
    assert await registry.get_status(task_id) == JobStatus.FAILED
    assert "job.failed" in [t.value for t in bus.published_types()]


# --- bot ------------------------------------------------------------------


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append(text)

    async def send_document(self, chat_id: int, document: Any) -> None:
        self.sent.append(document)


def _make_reporter(artifacts: Path, bot: _FakeBot) -> ResultReporter:
    from swot_bot.renderer import MessageRenderer

    return ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=1,
        renderer=MessageRenderer(),
        artifacts_dir=str(artifacts),
    )


@pytest.mark.asyncio
async def test_bot_rejects_summary_path_outside_artifacts(tmp_path: Path) -> None:
    """summary_path='/etc/passwd' → сообщение не падает, файл не читается."""
    bot = _FakeBot()
    reporter = _make_reporter(tmp_path / "artifacts", bot)

    await reporter.on_analysis(
        AnalysisReady(
            task_id=UUID("11111111-1111-1111-1111-111111111111"),
            trace_id="t-x",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir="/etc",
            summary_path="/etc/passwd",
        )
    )

    # T-1.7: вместо молчаливого проглатывания админ получает нормализованное
    # уведомление (без traceback); сам файл не читается и не рендерится.
    assert len(bot.sent) == 1
    assert "Не удалось доставить результат" in bot.sent[0]
    assert "Traceback" not in bot.sent[0]


@pytest.mark.asyncio
async def test_bot_happy_path_sends_text_and_srt(tmp_path: Path) -> None:
    """summary внутри artifacts → текст + srt отправляются."""
    artifacts = tmp_path / "artifacts"
    task_id = UUID("22222222-2222-2222-2222-222222222222")
    (artifacts / str(task_id)).mkdir(parents=True)
    (artifacts / str(task_id) / "summary.json").write_text(
        json.dumps(
            {
                "title": "Лекция",
                "summary": "Резюме",
                "sections": [
                    {
                        "heading": "Раздел",
                        "facts": [{"text": "Факт", "start_sec": 65, "end_sec": 70}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (artifacts / str(task_id) / "transcript.srt").write_text("srt", encoding="utf-8")

    bot = _FakeBot()
    reporter = _make_reporter(artifacts, bot)

    await reporter.on_analysis(
        AnalysisReady(
            task_id=task_id,
            trace_id="t-ok",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir=str(artifacts / str(task_id)),
            summary_path=str(artifacts / str(task_id) / "summary.json"),
        )
    )

    assert len(bot.sent) == 2  # текст + srt-документ
    assert "Лекция" in bot.sent[0]


# --- transcriber ------------------------------------------------------------


class _StubExtractor:
    async def extract(self, media: Path, out: Path) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"wav")
        return out


class _StubTranscriber:
    async def transcribe(self, audio: Path, out_dir: Path) -> Any:
        from swot_transcriber.domain import TranscriptResult

        out_dir.mkdir(parents=True, exist_ok=True)
        srt = out_dir / "transcript.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nТ\n", encoding="utf-8")
        segs = out_dir / "segments.json"
        segs.write_text("[]", encoding="utf-8")
        return TranscriptResult(
            base_dir=out_dir, srt_path=srt, segments_path=segs, language="ru"
        )


@pytest.mark.asyncio
async def test_transcriber_does_not_delete_media_outside_media_dir(
    tmp_path: Path,
) -> None:
    """media_path вне media_dir → файл не удаляется, задача FAILED."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    victim = tmp_path / "precious.bin"
    victim.write_bytes(b"do-not-touch")
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    svc = TranscribeService(
        artifacts_dir=str(tmp_path / "artifacts"),
        extractor=_StubExtractor(),
        transcriber=_StubTranscriber(),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        media_dir=str(media_dir),
    )

    task_id = uuid4()
    await svc.handle(
        VideoDownloaded(
            task_id=task_id,
            trace_id="t-esc",
            source=SourceRef(url="https://example.com/v.mp4"),
            media_path=str(victim),
            title="victim",
            duration_sec=1,
        )
    )

    assert victim.exists()  # файл не удалён
    assert await registry.get_status(task_id) == JobStatus.FAILED
    assert "job.failed" in [t.value for t in bus.published_types()]
