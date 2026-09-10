"""T-1.4 acceptance: ASR chunking (long audio -> per-chunk ASR calls, shifted
SRT timestamps, size limit before memory load) and LLM map-reduce chunking
(long transcript -> per-chunk calls + merged valid Summary, no dup facts)."""

import json
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from fakes import FakeBus
from swot_analyzer.domain import Fact, Section, SummarizeError, Summary
from swot_analyzer.summarizer import LlmSummarizer
from swot_bus import InMemoryJobRegistry
from swot_contracts import SourceRef, VideoDownloaded
from swot_transcriber.asr import OpenaiAsrTranscriber
from swot_transcriber.domain import TranscribeError
from swot_transcriber.service import TranscribeService

# ---------------------------------------------------------------------------
# ASR chunking
# ---------------------------------------------------------------------------


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _Result:
    def __init__(self, language: str) -> None:
        self.language = language
        self.text = ""
        self.duration = 5.0
        self.segments = [
            _Segment(0.0, 5.0, f"Фрагмент {len(_SegmentedClient.calls) - 1}")
        ]


class _SegmentedClient:
    """Fake ASR client: every call is one chunk, returns a local 0->5s segment."""

    calls: list[dict] = []

    def __init__(self) -> None:
        type(self).calls = []
        self._audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self._create)
        )

    @property
    def audio(self):
        return self._audio

    async def _create(self, **kwargs) -> _Result:
        type(self).calls.append(kwargs)
        return _Result("ru")


class _FakeSegmenter:
    """Emulates ffmpeg segmentation: N chunk files, each with local time base 0."""

    def __init__(self, n_chunks: int) -> None:
        self.n_chunks = n_chunks
        self.segmented = 0

    async def segment(
        self, audio_path: Path, out_dir: Path, segment_duration_sec: int
    ) -> list[Path]:
        self.segmented += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        chunks: list[Path] = []
        for i in range(self.n_chunks):
            chunk = out_dir / f"seg_{i:03d}.wav"
            chunk.write_bytes(b"pcm")
            chunks.append(chunk)
        return chunks


async def test_long_audio_splitted_per_segment_with_shifted_srt(tmp_path: Path) -> None:
    """3-часовое аудио (эмуляция: 18 чанков по 600 с) -> 18 ASR-вызовов,
    таймкоды SRT сдвинуты на index * 600 сек."""
    client = _SegmentedClient()
    segmenter = _FakeSegmenter(n_chunks=18)  # 18 * 10 минут = 3 часа
    transcriber = OpenaiAsrTranscriber(
        base_url="http://asr:8000/v1",
        api_key="k",
        model="whisper-1",
        client=client,
        segmenter=segmenter,
        segment_duration_sec=600,
        max_audio_mb=512,
        timeout_sec=300,
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"pcm")

    tr = await transcriber.transcribe(audio, tmp_path / "out")

    # по одному ASR-вызову на чанк (18 = 3 часа / 10 минут)
    calls = _SegmentedClient.calls
    assert len(calls) == 18
    # таймаут передаётся в каждый вызов
    assert all(call["timeout"] == 300 for call in calls)
    # сегментатор вызван один раз на всё аудио
    assert segmenter.segmented == 1

    # таймкоды сдвинуты: 1-й чанк с 00:00:00, 2-й с 00:10:00, 18-й с 02:50:00
    srt = tr.srt_path.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert "00:10:00,000 --> 00:10:05,000" in srt
    assert "02:50:00,000 --> 02:50:05,000" in srt

    segs = json.loads(tr.segments_path.read_text(encoding="utf-8"))
    assert len(segs) == 18
    assert segs[0]["start"] == 0.0
    assert segs[1]["start"] == 600.0
    assert segs[17]["start"] == 17 * 600.0


async def test_oversized_audio_rejected_before_memory_load(tmp_path: Path) -> None:
    """Аудио больше лимита -> TranscribeError ДО загрузки в память
    (сегментатор и ASR-клиент не трогаются)."""
    client = _SegmentedClient()
    segmenter = _FakeSegmenter(n_chunks=3)
    transcriber = OpenaiAsrTranscriber(
        base_url="http://asr:8000/v1",
        api_key="k",
        model="whisper-1",
        client=client,
        segmenter=segmenter,
        max_audio_mb=1,  # 1 MiB — легко превзойти в тесте
        timeout_sec=300,
    )
    audio = tmp_path / "big.wav"
    audio.write_bytes(b"\0" * (2 * 1024 * 1024))

    try:
        await transcriber.transcribe(audio, tmp_path / "out")
        raise AssertionError("expected TranscribeError")
    except TranscribeError as exc:
        assert "too large" in str(exc)
        assert "2 MB" in str(exc)
    assert segmenter.segmented == 0
    assert _SegmentedClient.calls == []


async def test_oversized_audio_publishes_job_failed(tmp_path: Path) -> None:
    """Service-level: превьюшкое аудио -> JobFailed с явной причиной."""
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    task_id = UUID("00000000-0000-0000-0000-000000000004")

    class _OversizedExtractor:
        async def extract(self, media_path: Path, dst: Path) -> Path:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(b"\0" * (2 * 1024 * 1024))
            return dst

    client = _SegmentedClient()
    svc = TranscribeService(
        artifacts_dir=str(tmp_path),
        extractor=_OversizedExtractor(),
        transcriber=OpenaiAsrTranscriber(
            base_url="http://asr:8000/v1",
            api_key="k",
            model="whisper-1",
            client=client,
            segmenter=_FakeSegmenter(n_chunks=1),
            max_audio_mb=1,
        ),
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        media_dir=str(tmp_path),
    )
    (tmp_path / "m.m4a").write_bytes(b"fake-media")
    await svc.handle(
        VideoDownloaded(
            task_id=task_id,
            trace_id="t-4",
            source=SourceRef(url="https://disk.yandex.ru/i/abc"),
            media_path=str(tmp_path / "m.m4a"),
            title="big",
            duration_sec=7200,
        )
    )
    assert "job.failed" in [t.value for t in bus.published_types()]
    failed = bus.get(1)
    assert failed.msg_type.value == "job.failed"
    assert "too large" in failed.error
    assert _SegmentedClient.calls == []


# ---------------------------------------------------------------------------
# LLM map-reduce chunking
# ---------------------------------------------------------------------------


def _prompt_text(inputs) -> str:
    """Вход после ChatPromptTemplate — ChatPromptValue/список сообщений.

    Берём content первого сообщения (без repr-экранирования)."""
    if isinstance(inputs, dict):
        return str(inputs.get("input", ""))
    messages = getattr(inputs, "messages", None)
    if messages is None and isinstance(inputs, (list, tuple)):
        messages = inputs
    if messages:
        return str(getattr(messages[0], "content", messages[0]))
    return str(inputs)


def _reduce_partials(inputs) -> list[dict]:
    """Массив частичных выжимок из reduce-входа (JSON внутри промпта)."""
    text = _prompt_text(inputs)
    partials, _ = json.JSONDecoder().raw_decode(text, text.index("["))
    return partials


class _FakeRunnable:
    """Mimics ``ChatOpenAI.with_structured_output(Summary)`` pipeline output.

    Map: из фрагмента транскрипта собирает факты ``ФАКТ: X``.
    Reduce: вход — промпт с JSON-массивом частичных Summary, склеивает
    в один Summary и дедуплицирует факты (контракт REDUCE_PROMPT).
    """

    def __init__(self, llm: "_FakeLLM") -> None:
        self._llm = llm

    async def __call__(self, inputs) -> Summary:
        text = _prompt_text(inputs)
        self._llm.inputs.append(text)
        if '"sections"' in text:  # reduce: JSON-массив частичных summary в промпте
            partials = _reduce_partials(inputs)
            facts: list[Fact] = []
            seen: dict[str, float] = {}
            for part in partials:
                for section in part["sections"]:
                    for f in section["facts"]:
                        if f["text"] not in seen:
                            seen[f["text"]] = f["start_sec"]
                            facts.append(Fact(text=f["text"], start_sec=f["start_sec"]))
            return Summary(
                summary="Объединённое резюме.",
                sections=[Section(heading="Объединено", facts=facts)],
            )
        # map: факты из фрагмента транскрипта
        facts = [
            Fact(text=f"ФАКТ: {m}", start_sec=float(i))
            for i, m in enumerate(re.findall(r"ФАКТ: (\S+)", text))
        ]
        return Summary(
            summary="Частичное резюме фрагмента.",
            sections=[Section(heading="Фрагмент", facts=facts)],
        )


class _FakeLLM:
    """Mimics the langchain ChatOpenAI surface used by LlmSummarizer."""

    def __init__(self) -> None:
        self.inputs: list[str] = []

    def with_structured_output(self, schema):  # noqa: ANN001
        return _FakeRunnable(self)


async def test_map_reduce_multiple_chunks_no_duplicate_facts(tmp_path: Path) -> None:
    """Транскрипт длиннее одного чанка: LLM вызывается N+1 раз (map+reduce),
    итоговый Summary — валидный Pydantic, дубликаты фактов устранены."""
    llm = _FakeLLM()
    summarizer = LlmSummarizer(
        base_url="http://llm:8000/v1",
        api_key="k",
        model="gpt-4o-mini",
        chunk_chars=20,  # маленький размер -> гарантированно несколько чанков
        max_transcript_chars=1000,
        llm=llm,
    )
    # "ФАКТ: A" встречается в ДВУХ фрагментах -> после reduce дубль исчезает
    transcript = "ФАКТ: A\nФАКТ: B\nФАКТ: A\nФАКТ: C\n"

    result = await summarizer.summarize(transcript, "Ниже транскрипт: {input}")

    assert len(llm.inputs) == 3  # 2 map + 1 reduce
    assert "ФАКТ:" in llm.inputs[0]  # map получил фрагменты транскрипта
    assert '"sections"' in llm.inputs[2]  # reduce получил частичные summary
    partials = _reduce_partials(llm.inputs[2])
    assert len(partials) == 2
    assert all("sections" in p for p in partials)
    # итог — валидный Pydantic-модель Summary
    assert isinstance(result, Summary)
    Summary.model_validate(result.model_dump())
    facts = [f.text for f in result.sections[0].facts]
    assert sorted(facts) == ["ФАКТ: A", "ФАКТ: B", "ФАКТ: C"]
    assert len(facts) == len(set(facts))  # дубликатов нет


async def test_long_transcript_splits_into_multiple_map_calls() -> None:
    """Реальный CharacterTextSplitter: длинный транскрипт -> несколько map-вызовов."""
    llm = _FakeLLM()
    summarizer = LlmSummarizer(
        base_url="http://llm:8000/v1",
        api_key="k",
        model="gpt-4o-mini",
        chunk_chars=8000,
        max_transcript_chars=100000,
        llm=llm,
    )
    transcript = "\n".join(
        f"ФАКТ: {i}" for i in range(3000)
    )  # ~27 Кб -> несколько чанков
    result = await summarizer.summarize(transcript, "Ниже транскрипт: {input}")

    # N map + 1 reduce (N >= 2: транскрипт длиннее chunk_chars)
    partials = _reduce_partials(llm.inputs[-1])
    n_chunks = len(partials)
    assert n_chunks >= 2
    assert len(llm.inputs) == n_chunks + 1
    assert isinstance(result, Summary)
    assert len(result.sections[0].facts) == 3000


async def test_short_transcript_single_call() -> None:
    """Короткий транскрипт -> один вызов (как раньше, без map-reduce)."""
    llm = _FakeLLM()
    summarizer = LlmSummarizer(
        base_url="http://llm:8000/v1",
        api_key="k",
        model="gpt-4o-mini",
        chunk_chars=8000,
        max_transcript_chars=200000,
        llm=llm,
    )
    result = await summarizer.summarize("ФАКТ: A\nФАКТ: B", "Ниже транскрипт: {input}")
    assert len(llm.inputs) == 1
    assert isinstance(result, Summary)
    assert [f.text for f in result.sections[0].facts] == ["ФАКТ: A", "ФАКТ: B"]


async def test_transcript_over_limit_rejected_without_llm_call() -> None:
    """Транскрипт больше лимита -> SummarizeError, LLM не вызывается."""
    llm = _FakeLLM()
    summarizer = LlmSummarizer(
        base_url="http://llm:8000/v1",
        api_key="k",
        model="gpt-4o-mini",
        chunk_chars=8000,
        max_transcript_chars=100,
        llm=llm,
    )
    try:
        await summarizer.summarize("ФАКТ: A\n" * 20, "Ниже транскрипт: {input}")
        raise AssertionError("expected SummarizeError")
    except SummarizeError as exc:
        assert "too large" in str(exc)
    assert llm.inputs == []


def test_chatopenai_gets_max_tokens_and_timeout() -> None:
    """LLM-вызовы ограничены max_tokens и timeout (P1-7)."""
    summarizer = LlmSummarizer(
        base_url="http://llm:8000/v1",
        api_key="k",
        model="gpt-4o-mini",
        max_tokens=4096,
        timeout_sec=120,
    )
    model = summarizer._model  # noqa: SLF001
    assert model.max_tokens == 4096
    assert model.request_timeout == 120
