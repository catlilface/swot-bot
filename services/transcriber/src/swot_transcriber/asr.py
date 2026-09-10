"""OpenAI-compatible ASR Transcriber implementation + FakeTranscriber.

Long audio is chunked (ffmpeg ``segment`` muxer, ``Segmenter`` port) so each
ASR call uploads at most ~10–15 minutes of audio instead of the whole
multi-hundred-MB WAV, and per-chunk SRT timestamps are shifted by
``chunk_index * segment_duration_sec`` when the final SRT is assembled.
"""

import json
from pathlib import Path
from typing import Any

from .domain import Segmenter, TranscribeError, TranscriptResult
from .ffmpeg import FfmpegSegmenter


def _fmt_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class OpenaiAsrTranscriber:
    """Transcribe via an OpenAI-compatible endpoint (POST /v1/audio/transcriptions).

    Each chunk is one ``verbose_json`` call (bounded upload size, bounded
    ``timeout``); the per-chunk segment timings are shifted to global
    timestamps and merged into ``transcript.srt`` + ``segments.json``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        language: str = "",
        client: Any | None = None,
        segmenter: Segmenter | None = None,
        segment_duration_sec: int = 600,
        max_audio_mb: int = 512,
        timeout_sec: int = 300,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = client or AsyncOpenAI(
            base_url=base_url, api_key=api_key or "none"
        )
        self._model = model
        self._language = language
        self._segmenter = segmenter or FfmpegSegmenter()
        self._segment_duration_sec = segment_duration_sec
        self._max_audio_bytes = max_audio_mb * 1024 * 1024
        self._timeout_sec = timeout_sec

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        self._check_size(audio_path)

        chunks = await self._segmenter.segment(
            audio_path, out_dir / "chunks", self._segment_duration_sec
        )

        records: list[dict] = []
        language = self._language
        for idx, chunk in enumerate(chunks):
            offset = idx * self._segment_duration_sec
            with chunk.open("rb") as f:
                result = await self._client.audio.transcriptions.create(  # type: ignore[call-overload]  # stubs openai не принимают кортеж file вместе с verbose_json (рабочая форма API)
                    model=self._model,
                    file=(chunk.name, f, "audio/wav"),
                    response_format="verbose_json",
                    language=self._language or None,
                    timeout=self._timeout_sec,
                )
            language = getattr(result, "language", "") or language
            records.extend(_shift_records(_records_from_result(result), offset))

        srt_path = out_dir / "transcript.srt"
        segments_path = out_dir / "segments.json"
        srt_path.write_text(
            "\n".join(_srt_lines_from_records(records)), encoding="utf-8"
        )
        segments_path.write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8"
        )
        return TranscriptResult(
            base_dir=out_dir,
            srt_path=srt_path,
            segments_path=segments_path,
            language=language,
        )

    def _check_size(self, audio_path: Path) -> None:
        """Reject oversized audio *before* any bytes are loaded into memory."""
        size = audio_path.stat().st_size
        if size <= self._max_audio_bytes:
            return
        limit_mb = self._max_audio_bytes // (1024 * 1024)
        raise TranscribeError(
            f"audio file too large: {size / (1024 * 1024):.0f} MB "
            f"(limit {limit_mb} MB); refusing to load into memory"
        )


def _records_from_result(result: Any) -> list[dict]:
    """Segment records (chunk-local times) from a verbose_json transcription."""
    records = [
        {
            "start": round(float(seg.start), 2),
            "end": round(float(seg.end), 2),
            "text": str(seg.text),
        }
        for seg in getattr(result, "segments", None) or []
    ]
    if not records:
        # Endpoint did not return segments: single cue for the whole chunk.
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            end = float(getattr(result, "duration", 0.0) or 0.0)
            records = [{"start": 0.0, "end": round(end, 2), "text": text}]
    return records


def _shift_records(records: list[dict], offset: float) -> list[dict]:
    """Shift chunk-local timestamps to global (offset = chunk_index * chunk_len)."""
    if not offset:
        return records
    return [
        {
            "start": round(rec["start"] + offset, 2),
            "end": round(rec["end"] + offset, 2),
            "text": rec["text"],
        }
        for rec in records
    ]


def _srt_lines_from_records(records: list[dict]) -> list[str]:
    lines: list[str] = []
    for i, rec in enumerate(records, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(rec['start'])} --> {_fmt_srt_time(rec['end'])}")
        lines.append(rec["text"])
        lines.append("")
    return lines


class FakeTranscriber:
    """Deterministic transcriber for tests (no network / no ASR endpoint)."""

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        srt = out_dir / "transcript.srt"
        segs = out_dir / "segments.json"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\nПривет, это тестовая лекция.\n\n",
            encoding="utf-8",
        )
        segs.write_text(
            json.dumps(
                [{"start": 0.0, "end": 5.0, "text": "Привет, это тестовая лекция."}]
            ),
            encoding="utf-8",
        )
        return TranscriptResult(
            base_dir=out_dir,
            srt_path=srt,
            segments_path=segs,
            language="ru",
        )
