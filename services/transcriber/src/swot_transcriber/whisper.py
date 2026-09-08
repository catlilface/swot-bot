"""Faster-whisper Transcriber implementation (GPU) + FakeTranscriber."""

import json
from pathlib import Path

from .domain import TranscriptResult


def _fmt_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


class FasterWhisperTranscriber:
    """Transcribe via faster-whisper (CTranslate2, GPU by default)."""

    def __init__(
        self,
        model_size: str = "distil-large-v3",
        device: str = "cuda",
        compute_type: str = "int8_float16",
        language: str = "",
        batch_size: int = 4,
        models_dir: str = "/data/models",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._batch_size = batch_size
        self._models_dir = models_dir
        self._model = None

    def _load(self):
        from faster_whisper import WhisperModel

        if self._model is None:
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
                download_root=self._models_dir,
            )
        return self._model

    async def transcribe(self, audio_path: Path, out_dir: Path) -> TranscriptResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        segments, info = self._load().transcribe(
            str(audio_path),
            language=self._language or None,
            batch_size=self._batch_size if self._device == "cuda" else 1,
        )
        segs = list(segments)

        srt_path = out_dir / "transcript.srt"
        segments_path = out_dir / "segments.json"

        srt_lines: list[str] = []
        seg_records: list[dict] = []
        for i, seg in enumerate(segs, start=1):
            srt_lines.append(str(i))
            srt_lines.append(
                f"{_fmt_srt_time(seg.start)} --> {_fmt_srt_time(seg.end)}"
            )
            srt_lines.append(seg.text.strip())
            srt_lines.append("")
            seg_records.append(
                {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": seg.text}
            )

        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        segments_path.write_text(
            json.dumps(seg_records, ensure_ascii=False), encoding="utf-8"
        )
        return TranscriptResult(
            base_dir=out_dir,
            srt_path=srt_path,
            segments_path=segments_path,
            language=info.language or self._language,
        )


class FakeTranscriber:
    """Deterministic transcriber for tests (no GPU / no model)."""

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
