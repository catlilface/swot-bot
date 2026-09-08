"""Transcriber service domain: port + result model."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TranscriptResult:
    """What a transcriber produced for one media file."""

    base_dir: Path
    srt_path: Path
    segments_path: Path
    language: str = ""


class AudioExtractor(Protocol):
    """Extract 16kHz mono wav from arbitrary media."""

    async def extract(self, media_path: Path, dst: Path) -> Path: ...


class Transcriber(Protocol):
    """Transcribe an audio file into SRT + segments.json."""

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult: ...
