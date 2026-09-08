"""ffmpeg-based audio extraction (16kHz mono wav)."""

import asyncio
from pathlib import Path


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
            msg = stderr.decode(errors="replace")[:1000]
            raise RuntimeError(f"ffmpeg failed: {msg}")
        return dst