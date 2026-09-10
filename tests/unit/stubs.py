"""Shared test doubles for service tests (T-2.7: no duplicate stubs).

Import as ``from stubs import StubRouter, ...`` — the ``tests/unit`` directory
is on ``sys.path`` during pytest runs (rootdir import mode).
"""

from pathlib import Path

from swot_analyzer.domain import Fact, Section, Summary
from swot_contracts import SourceRef
from swot_downloader.domain import DownloadedMedia
from swot_transcriber.domain import TranscriptResult


class StubRouter:
    """Stand-in for SourceRouter: writes a fake media file, records calls."""

    def __init__(
        self,
        tmp_path: Path | None = None,
        *,
        filename: str = "media.m4a",
        payload: bytes = b"fake-audio",
        title: str = "stub",
        duration_sec: int = 60,
        resource_id: str = "r1",
    ) -> None:
        self.tmp_path = tmp_path
        self.filename = filename
        self.payload = payload
        self.title = title
        self.duration_sec = duration_sec
        self.resource_id = resource_id
        self.calls: list[SourceRef] = []

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        self.calls.append(source)
        dst_dir.mkdir(parents=True, exist_ok=True)
        f = dst_dir / self.filename
        f.write_bytes(self.payload)
        return DownloadedMedia(
            media_path=f,
            title=self.title,
            duration_sec=self.duration_sec,
            resource_id=self.resource_id,
        )


class StubExtractor:
    """Writes a fake extracted audio file."""

    async def extract(self, media_path: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"pcm")
        return dst


class StubTranscriber:
    """Writes a minimal SRT + segments.json and returns a TranscriptResult."""

    def __init__(self, text: str = "Тест.", end: int = 2, language: str = "ru") -> None:
        self.text = text
        self.end = end
        self.language = language

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        srt = out_dir / "transcript.srt"
        segs = out_dir / "segments.json"
        srt.write_text(
            f"1\n00:00:00,000 --> 00:00:{self.end:02d},000\n{self.text}\n",
            encoding="utf-8",
        )
        segs.write_text(
            f'[{{"start": 0, "end": {self.end}, "text": "{self.text}"}}]',
            encoding="utf-8",
        )
        return TranscriptResult(
            base_dir=out_dir, srt_path=srt, segments_path=segs, language=self.language
        )


def _default_summary() -> Summary:
    return Summary(
        summary="Резюме",
        sections=[
            Section(
                heading="Гл1", facts=[Fact(text="Факт из транскрипта", start_sec=5)]
            )
        ],
    )


class StubSummarizer:
    """Returns a fixed Summary (default matches test_analyzer expectations)."""

    def __init__(self, summary: Summary | None = None) -> None:
        self.summary = summary if summary is not None else _default_summary()

    async def summarize(self, transcript: str, prompt: str) -> Summary:
        return self.summary


class LocalPrompt:
    """PromptProvider double: any prompt name -> one fixed prompt."""

    def get(self, name: str) -> str:
        return "промпт"
