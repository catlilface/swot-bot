"""Unit tests for the bot service: validator, renderer, admin filter."""

import json
from pathlib import Path
from typing import Any

from swot_bot.handlers import _chunk_text, _kind_for
from swot_bot.renderer import MessageRenderer
from swot_bot.validation import UrlValidator


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
