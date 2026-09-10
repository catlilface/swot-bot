"""Unit tests for the bot service: validator, renderer, admin filter."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from swot_bot.handlers import ResultReporter, _chunk_text, _kind_for
from swot_bot.renderer import MessageRenderer
from swot_bot.validation import UrlValidator
from swot_contracts import JobProgress


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
