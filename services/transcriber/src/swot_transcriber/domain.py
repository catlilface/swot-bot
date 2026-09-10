"""Transcriber service domain: ports + result model."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TranscribeError(Exception):
    """Transcription failure (audio over size limit, ffmpeg/ASR call failed)."""


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


class Segmenter(Protocol):
    """Split a long audio file into chunks of at most ``segment_duration_sec``.

    Returns an ordered list of chunk files; each chunk's internal timestamps
    start at 0, so the transcriber shifts ASR timestamps by
    ``chunk_index * segment_duration_sec`` when assembling the final SRT.
    """

    async def segment(
        self, audio_path: Path, out_dir: Path, segment_duration_sec: int
    ) -> list[Path]: ...
