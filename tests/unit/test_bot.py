"""Unit tests for the bot service: validator, renderer, admin filter."""

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetMe
from swot_bot.handlers import ResultReporter, _chunk_text, _kind_for
from swot_bot.renderer import MessageRenderer
from swot_bot.validation import UrlValidator
from swot_contracts import AnalysisReady, JobFailed, JobProgress, SourceRef


def _make_summary() -> dict[str, Any]:
    return {
        "title": "Лекция",
        "summary": "Резюме",
        "sections": [
            {
                "heading": "Раздел",
                "facts": [{"text": "Факт", "start_sec": 65, "end_sec": 70}],
            }
        ],
    }


def test_url_validator_accepts_any_source() -> None:
    v = UrlValidator()
    assert v.validate("https://disk.yandex.ru/i/abc") == "https://disk.yandex.ru/i/abc"
    assert v.validate("https://example.com/video.mp4")
    try:
        v.validate("ftp://x/y")
    except ValueError:
        pass
    else:
        raise AssertionError("ftp must be rejected")


def test_url_validator_normalizes_netloc() -> None:
    """T-2.5: субдомен и стандартный порт не ломают логическое совпадение."""
    v = UrlValidator("youtube.com,rutube.ru")
    # субдомен разрешённого домена
    assert v.validate("https://m.youtube.com/watch?v=1")
    # стандартный порт схемы — тот же endpoint
    assert v.validate("https://youtube.com:443/watch?v=1")
    # регистр не важен
    assert v.validate("https://M.YOUTUBE.COM/watch?v=1")
    # нестандартный порт — другой endpoint, не совпадает
    with pytest.raises(ValueError):
        v.validate("https://youtube.com:8443/x")
    # ни суффикс, ни субдомен разрешённого домена
    with pytest.raises(ValueError):
        v.validate("https://youtu.be/abc")
    # граница суффикса: notyoutube.com НЕ субдомен youtube.com
    with pytest.raises(ValueError):
        v.validate("https://notyoutube.com/x")
    # другой домен
    with pytest.raises(ValueError):
        v.validate("https://malicious.com/x")


def test_chunk_text_limits() -> None:
    long = "x" * 9000
    chunks = _chunk_text(long)
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == long


class RecordingBot:
    """Minimal double of aiogram.Bot: records send_message calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> str:
        self.sent.append((chat_id, text))
        return text


async def test_bot_progress_labels_and_fallback() -> None:
    """T-1.6: known stages — label with emoji, unknown stage — the name itself (no crash)."""
    bot = RecordingBot()
    reporter = ResultReporter(
        bot=bot,
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir="/tmp",
    )
    task_id = UUID("00000000-0000-0000-0000-000000000011")
    for stage, _expected in (
        ("downloading", "⬇️ Скачиваю… (downloading)"),
        ("transcribing", "📝 Транскрибирую… (transcribing)"),
        ("analyzing", "🧠 Анализирую… (analyzing)"),
        ("weird_stage", "weird_stage (weird_stage)"),
    ):
        await reporter.on_progress(
            JobProgress(task_id=task_id, trace_id="t-11", stage=stage)
        )
    assert [t for _, t in bot.sent] == [
        "⬇️ Скачиваю… (downloading)",
        "📝 Транскрибирую… (transcribing)",
        "🧠 Анализирую… (analyzing)",
        "weird_stage (weird_stage)",
    ]
    assert all(chat == 42 for chat, _ in bot.sent)


def test_kind_for() -> None:
    assert _kind_for("https://disk.yandex.ru/i/abc") == "yandex_disk"
    assert _kind_for("https://drive.google.com/file/x") == "gdrive"
    assert _kind_for("https://rutube.ru/video/x") == "rutube"
    assert _kind_for("https://example.com/v.mp4") == "generic"


def test_renderer_includes_time_and_fact(tmp_path: Path) -> None:
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(_make_summary()), encoding="utf-8")
    text = MessageRenderer().render(p)
    assert "Лекция" in text
    assert "Факт" in text
    # 65s -> 01:05
    assert "01:05" in text


# --- T-2.4: title, SRT из ивента, кэш шаблонов -----------------------------


def test_renderer_caches_templates(tmp_path: Path) -> None:
    """T-2.4: шаблон компилируется один раз; подмена файла не перечитывается."""
    tdir = tmp_path / "templates"
    tdir.mkdir()
    tpl = tdir / "result_message.html.j2"
    tpl.write_text("Первая: {{ summary.title }}", encoding="utf-8")
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"title": "X"}), encoding="utf-8")
    r = MessageRenderer(template_dir=tdir)
    assert r.render(summary) == "Первая: X"
    # Файл шаблона изменился на диске — рендер остаётся на кэше
    tpl.write_text("Вторая: {{ summary.title }}", encoding="utf-8")
    assert r.render(summary) == "Первая: X"


class RecordingDocumentBot:
    """aiogram.Bot double: records both messages and documents."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.documents: list[str] = []  # filename

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> str:
        self.messages.append(text)
        return text

    async def send_document(self, chat_id: int, document: Any, **kwargs: Any) -> None:
        self.documents.append(document.filename)


async def test_title_from_summary_reaches_bot_message(tmp_path: Path) -> None:
    """T-2.4: заголовок из download (в summary.json) попал в сообщение бота."""
    artifacts = tmp_path / "artifacts"
    task_id = UUID("00000000-0000-0000-0000-000000000021")
    (artifacts / str(task_id)).mkdir(parents=True)
    (artifacts / str(task_id) / "summary.json").write_text(
        json.dumps(
            {
                "title": "Квантовая механика: основы",
                "summary": "Резюме",
                "sections": [
                    {"heading": "Р1", "facts": [{"text": "Ф", "start_sec": 0}]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bot = RecordingDocumentBot()
    reporter = ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir=str(artifacts),
        retry_delays=(),
    )
    await reporter.on_analysis(
        AnalysisReady(
            task_id=task_id,
            trace_id="t-21",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir=str(artifacts / str(task_id)),
            summary_path=str(artifacts / str(task_id) / "summary.json"),
            title="Квантовая механика: основы",
            srt_path="",
        )
    )
    assert len(bot.messages) == 1
    assert "Квантовая механика: основы" in bot.messages[0]
    assert bot.documents == []


async def test_bot_uses_srt_path_from_event(tmp_path: Path) -> None:
    """T-2.4: SRT доставляется по пути из ивента, а не пересобранный из
    конфига бота: меняем «TRANSCRIBER__ARTIFACTS_DIR» (layout tasks/<id>) —
    доставка не ломается."""
    root = tmp_path / "shared"  # общий том: и бота, и транскрибера
    task_id = UUID("00000000-0000-0000-0000-000000000022")
    (root / str(task_id)).mkdir(parents=True)
    (root / str(task_id) / "summary.json").write_text(
        json.dumps(
            {"title": "Л", "summary": "Р", "sections": []}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    # Транскрибер пишет SRT в своём layout: <transcriber_dir>/<task>/transcript.srt
    srt_dir = root / "tasks" / str(task_id)
    srt_dir.mkdir(parents=True)
    srt = srt_dir / "transcript.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nТекст\n", encoding="utf-8")

    bot = RecordingDocumentBot()
    reporter = ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir=str(root),  # бот «думает», что его artifacts — корень тома
        retry_delays=(),
    )
    await reporter.on_analysis(
        AnalysisReady(
            task_id=task_id,
            trace_id="t-22",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir=str(root / str(task_id)),
            summary_path=str(root / str(task_id) / "summary.json"),
            title="Л",
            srt_path=str(srt),  # путь из ивента — вне старого layout бота
        )
    )
    assert len(bot.messages) == 1
    assert bot.documents == ["transcript.srt"]


# --- T-1.7: доставка (ретраи, nack, нормализованные сообщения) -------------


def _retry_after() -> TelegramRetryAfter:
    """429 flood-control error as aiogram raises it."""
    return TelegramRetryAfter(
        method=GetMe(), message="Too Many Requests: flood control", retry_after=0
    )


class FlakyBot:
    """aiogram.Bot double: fails the first N sends with a 429, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.sent: list[str] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise _retry_after()
        self.sent.append(text)


class NackBus:
    """Fake bus with ack/nack semantics: handler OK → ack, raise → nack.

    Mirrors RabbitMessageBus._dispatch: an unhandled handler exception means
    the message is nacked (requeue/DLQ at bus level), not swallowed.
    """

    def __init__(self) -> None:
        self._handler: Callable[[Any], Awaitable[None]] | None = None
        self.acks = 0
        self.nacks = 0

    async def consume(self, handler: Callable[[Any], Awaitable[None]]) -> None:
        self._handler = handler

    async def publish(self, message: Any) -> None:
        assert self._handler is not None
        try:
            await self._handler(message)
            self.acks += 1
        except Exception:  # noqa: BLE001 - nack semantics
            self.nacks += 1


async def test_delivery_rate_limited_twice_then_delivered_no_nack(
    tmp_path: Path,
) -> None:
    """T-1.7: отправка падает 2 раза (rate limit) и succeeds → доставлено, nack не идёт."""
    bot = FlakyBot(failures=2)
    reporter = ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir=str(tmp_path),
        retry_delays=(0.0, 0.0, 0.0),
    )
    bus = NackBus()
    await bus.consume(reporter.on_failed)
    await bus.publish(
        JobFailed(
            task_id=UUID("00000000-0000-0000-0000-0000000000f1"),
            trace_id="t-retry",
            stage="download",
            error="transient",
        )
    )
    assert bot.calls == 3  # 2 transient failures + 1 success
    assert len(bot.sent) == 1  # доставлено
    assert (bus.acks, bus.nacks) == (1, 0)  # nack не идёт


async def test_delivery_retries_exhausted_then_nack(tmp_path: Path) -> None:
    """T-1.7: ретраи исчерпаны → raise → bus делает nack (retry/DLQ выше)."""
    bot = FlakyBot(failures=99)  # Telegram «лежит»
    reporter = ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir=str(tmp_path),
        retry_delays=(0.0, 0.0),
    )
    bus = NackBus()
    await bus.consume(reporter.on_failed)
    await bus.publish(
        JobFailed(
            task_id=UUID("00000000-0000-0000-0000-0000000000f2"),
            trace_id="t-retry2",
            stage="download",
            error="always down",
        )
    )
    assert bot.calls == 3  # 1 первичный + 2 ретрая
    assert bot.sent == []
    assert (bus.acks, bus.nacks) == (0, 1)  # nack


async def test_missing_summary_gets_normalized_notice(tmp_path: Path) -> None:
    """T-1.7: summary_path не существует → нормализованное сообщение, без traceback."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bot = RecordingBot()
    reporter = ResultReporter(
        bot=bot,  # type: ignore[arg-type]
        target_chat_id=42,
        renderer=MessageRenderer(),
        artifacts_dir=str(artifacts),
        retry_delays=(),
    )
    task_id = UUID("00000000-0000-0000-0000-0000000000f3")
    await reporter.on_analysis(
        AnalysisReady(
            task_id=task_id,
            trace_id="t-missing",
            source=SourceRef(url="https://example.com/v.mp4"),
            base_dir=str(artifacts / str(task_id)),
            summary_path=str(artifacts / str(task_id) / "summary.json"),
        )
    )
    assert len(bot.sent) == 1
    notice = bot.sent[0][1]
    assert "Не удалось доставить результат" in notice
    assert "Traceback" not in notice


def test_chunk_text_keeps_tags_and_entities_intact() -> None:
    """T-1.7: chunk_text сохраняет текст точно; тег/сущность не разрывается пополам."""
    unit = "<b>Раздел</b>\nФакт с <i>акцентом</i> и &amp; знаком\n"
    original = unit * 300  # ≈ 14.5KB → несколько чанков
    chunks = _chunk_text(original)
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == original
    for chunk in chunks:
        # «<...» без закрывающего '>' в конце чанка → тег разорван
        assert re.search(r"<[^>]*$", chunk) is None
        # сиротное '>' в начале → тег из предыдущего чанка
        assert not chunk.startswith(">")
        # повисшая &entity без ';' в конце чанка
        assert re.search(r"&[a-zA-Z#][a-zA-Z0-9]*$", chunk) is None


def test_chunk_text_cut_lands_on_tag_boundary() -> None:
    """T-1.7: граница лимита внутри тега → резалка откатывается перед тегом."""
    original = "x" * 4090 + "<b>bold</b>\n" + "y" * 4090 + "\n"
    chunks = _chunk_text(original)
    assert "".join(chunks) == original
    assert all(len(c) <= 4096 for c in chunks)
    for c in chunks:
        assert re.search(r"<[^>]*$", c) is None
        assert not c.startswith(">")
