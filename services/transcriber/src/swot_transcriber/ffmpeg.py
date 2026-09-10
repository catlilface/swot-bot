"""ffmpeg-based audio extraction (16kHz mono wav) + long-audio chunking."""

import asyncio
from pathlib import Path

from .domain import TranscribeError


class FfmpegAudioExtractor:
    """Extract mono 16 kHz wav to a working dir via ffmpeg subprocess."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self._ffmpeg = ffmpeg_bin

    async def extract(self, media_path: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            str(media_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            str(dst),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            # ffmpeg prints a version banner first; the actual error is at the tail.
            msg = stderr.decode(errors="replace").strip()[-500:]
            raise RuntimeError(f"ffmpeg failed: {msg}")
        return dst


class FfmpegSegmenter:
    """Split a long audio file into ~N-second chunks (ffmpeg ``segment`` muxer).

    Chunks are named ``seg_000.wav``, ``seg_001.wav``, … in time order; each
    one is a standalone file with its internal timestamps starting at 0
    (``-reset_timestamps 1``), which is what makes the per-chunk ASR results
    shiftable: global offset = ``chunk_index * segment_duration_sec``.
    """

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self._ffmpeg = ffmpeg_bin

    async def segment(
        self, audio_path: Path, out_dir: Path, segment_duration_sec: int
    ) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        pattern = str(out_dir / "seg_%03d.wav")
        cmd = [
            self._ffmpeg,
            "-y",
            "-i",
            str(audio_path),
            "-vn",
            "-f",
            "segment",
            "-segment_time",
            str(segment_duration_sec),
            "-reset_timestamps",
            "1",
            "-c",
            "copy",
            pattern,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            msg = stderr.decode(errors="replace").strip()[-500:]
            raise TranscribeError(f"ffmpeg segmentation failed: {msg}")
        chunks = sorted(out_dir.glob("seg_*.wav"))
        if not chunks:
            raise TranscribeError("ffmpeg produced no audio segments")
        return chunks
