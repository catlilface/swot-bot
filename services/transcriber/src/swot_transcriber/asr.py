"""OpenAI-compatible ASR Transcriber implementation + FakeTranscriber."""

import json
from pathlib import Path
from typing import Any

from .domain import TranscriptResult


def _fmt_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class OpenaiAsrTranscriber:
    """Transcribe via an OpenAI-compatible endpoint (POST /v1/audio/transcriptions).

    One call with ``response_format="verbose_json"`` returns both the full text
    and per-segment timings, which are enough to produce ``transcript.srt``
    and ``segments.json``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        language: str = "",
        client: Any | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = client or AsyncOpenAI(
            base_url=base_url, api_key=api_key or "none"
        )
        self._model = model
        self._language = language

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        with audio_path.open("rb") as f:
            result = await self._client.audio.transcriptions.create(
                model=self._model,
                file=("audio.wav", f, "audio/wav"),
                response_format="verbose_json",
                language=self._language or None,
            )

        srt_lines, seg_records = _to_srt_and_segments(result)

        srt_path = out_dir / "transcript.srt"
        segments_path = out_dir / "segments.json"

        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        segments_path.write_text(
            json.dumps(seg_records, ensure_ascii=False), encoding="utf-8"
        )
        return TranscriptResult(
            base_dir=out_dir,
            srt_path=srt_path,
            segments_path=segments_path,
            language=getattr(result, "language", "") or self._language,
        )


def _to_srt_and_segments(result: Any) -> tuple[list[str], list[dict]]:
    """Build SRT lines + segment records from a verbose_json transcription."""
    srt_lines: list[str] = []
    seg_records: list[dict] = []
    for i, seg in enumerate(getattr(result, "segments", None) or [], start=1):
        srt_lines.append(str(i))
        srt_lines.append(f"{_fmt_srt_time(seg.start)} --> {_fmt_srt_time(seg.end)}")
        srt_lines.append(str(seg.text).strip())
        srt_lines.append("")
        seg_records.append(
            {
                "start": round(float(seg.start), 2),
                "end": round(float(seg.end), 2),
                "text": str(seg.text),
            }
        )

    if not seg_records:
        # Endpoint did not return segments: single cue for the whole audio.
        text = str(getattr(result, "text", "") or "").strip()
        if text:
            end = float(getattr(result, "duration", 0.0) or 0.0)
            srt_lines = ["1", f"00:00:00,000 --> {_fmt_srt_time(end)}", text, ""]
            seg_records = [{"start": 0.0, "end": round(end, 2), "text": text}]

    return srt_lines, seg_records


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
