"""Unit tests for the OpenAI-compatible ASR transcriber (stubbed client)."""

import json
from pathlib import Path

from swot_transcriber.asr import OpenaiAsrTranscriber


class _FakeSegmenter:
    """Segmenter that returns the audio file itself as a single chunk."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, int]] = []

    async def segment(
        self, audio_path: Path, out_dir: Path, segment_duration_sec: int
    ) -> list[Path]:
        self.calls.append((audio_path, out_dir, segment_duration_sec))
        return [audio_path]


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _Result:
    def __init__(
        self,
        text: str,
        language: str,
        duration: float,
        segments: list[_Segment] | None,
    ) -> None:
        self.text = text
        self.language = language
        self.duration = duration
        self.segments = segments or []


class _Transcriptions:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> _Result:
        self.calls.append(kwargs)
        return self.result


class _Audio:
    def __init__(self, result: _Result) -> None:
        self.transcriptions = _Transcriptions(result)


class _Client:
    def __init__(self, result: _Result) -> None:
        self.audio = _Audio(result)


def _make(
    result: _Result, tmp_path: Path
) -> tuple[OpenaiAsrTranscriber, _Client, Path]:
    client = _Client(result)
    segmenter = _FakeSegmenter()
    transcriber = OpenaiAsrTranscriber(
        base_url="http://asr:8000/v1",
        api_key="k",
        model="whisper-1",
        language="",
        client=client,
        segmenter=segmenter,
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"pcm")
    return transcriber, client, audio


async def test_transcribe_writes_srt_and_segments(tmp_path: Path) -> None:
    result = _Result(
        text="Привет. Это тест.",
        language="ru",
        duration=5.5,
        segments=[
            _Segment(0.0, 2.5, "Привет."),
            _Segment(2.5, 5.5, "Это тест."),
        ],
    )
    transcriber, client, audio = _make(result, tmp_path)
    out_dir = tmp_path / "out"

    tr = await transcriber.transcribe(audio, out_dir)

    # вызов OpenAI-совместимого API с верными параметрами
    calls = client.audio.transcriptions.calls
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == "whisper-1"
    # "json" — самый совместимый дефолт (OpenAI, OpenRouter, whisper-server);
    # сегментные таймкоды в SRT требуют "verbose_json" (не все эндпоинты).
    assert call["response_format"] == "json"
    assert call["language"] == "ru"  # пустой конфиг -> дефолт "ru"
    filename, fileobj, mimetype = call["file"]
    assert filename == "audio.wav"
    assert mimetype == "audio/wav"
    assert isinstance(fileobj, object)

    # артефакты созданы
    assert tr.srt_path == out_dir / "transcript.srt"
    assert tr.segments_path == out_dir / "segments.json"
    assert tr.srt_path.exists() and tr.segments_path.exists()
    assert tr.language == "ru"

    srt = tr.srt_path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "Привет." in srt
    assert "00:00:02,500 --> 00:00:05,500" in srt

    segs = json.loads(tr.segments_path.read_text(encoding="utf-8"))
    assert segs == [
        {"start": 0.0, "end": 2.5, "text": "Привет."},
        {"start": 2.5, "end": 5.5, "text": "Это тест."},
    ]


async def test_transcribe_passes_configured_language(tmp_path: Path) -> None:
    result = _Result("hi", "", 1.0, [_Segment(0.0, 1.0, "hi")])
    client = _Client(result)
    transcriber = OpenaiAsrTranscriber(
        base_url="http://asr:8000/v1",
        api_key=None,
        model="whisper-1",
        language="en",
        client=client,
        segmenter=_FakeSegmenter(),
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"pcm")

    tr = await transcriber.transcribe(audio, tmp_path / "out")
    call = client.audio.transcriptions.calls[0]
    assert call["language"] == "en"
    # язык ответа пуст -> берём из конфига
    assert tr.language == "en"


async def test_transcribe_without_segments_falls_back_to_text(
    tmp_path: Path,
) -> None:
    result = _Result("Только текст без сегментов.", "ru", 7.25, None)
    transcriber, _, audio = _make(result, tmp_path)

    tr = await transcriber.transcribe(audio, tmp_path / "out")

    srt = tr.srt_path.read_text(encoding="utf-8")
    assert "Только текст без сегментов." in srt
    # единый титр на всю длительность
    assert "00:00:00,000 --> 00:00:07,250" in srt
    assert tr.language == "ru"


async def test_transcribe_response_format_configurable(tmp_path: Path) -> None:
    stub = _Client(_Result("Текст", "ru", 5.0, None))
    verbose = OpenaiAsrTranscriber(
        base_url="http://asr:8000/v1",
        api_key="k",
        model="whisper-1",
        language="",
        client=stub,
        segmenter=_FakeSegmenter(),
        response_format="verbose_json",
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"pcm")

    await verbose.transcribe(audio, tmp_path / "out")
    call = stub.audio.transcriptions.calls[0]
    assert call["response_format"] == "verbose_json"


async def test_transcribe_json_response_without_duration_falls_back_to_chunk_length(
    tmp_path: Path,
) -> None:
    # response_format=json: segments/duration нет -> единый титр до номинальной
    # длины чанка (600s), а не нулевой.
    result = _Result("Текст чанка.", "ru", 0.0, None)
    transcriber, _, audio = _make(result, tmp_path)

    tr = await transcriber.transcribe(audio, tmp_path / "out")

    srt = tr.srt_path.read_text(encoding="utf-8")
    assert "Текст чанка." in srt
    assert "00:00:00,000 --> 00:10:00,000" in srt
