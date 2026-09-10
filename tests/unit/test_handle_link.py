"""Ветки валидации handle_link (T-2.7): невалидный ввод → publish не происходит."""

import pytest
from fakes import FakeBus
from swot_bot.handlers import handle_link
from swot_bus import InMemoryJobRegistry
from swot_contracts import UrlValidator


class _LinkMessage:
    """Минимальный двой aiogram Message для прямого вызова handle_link."""

    def __init__(self, text: str | None) -> None:
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


@pytest.mark.asyncio
async def test_handle_link_non_url_text_replies_hint() -> None:
    message = _LinkMessage("привет")
    bus = FakeBus()
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator())
    assert message.answers == ["Пришли ссылку на лекцию."]
    assert bus.published == []


@pytest.mark.asyncio
async def test_handle_link_empty_text_replies_hint() -> None:
    message = _LinkMessage(None)
    bus = FakeBus()
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator())
    assert message.answers == ["Пришли ссылку на лекцию."]
    assert bus.published == []


@pytest.mark.asyncio
async def test_handle_link_invalid_url_replies_error_without_publish() -> None:
    # http(s)-префикс есть (первая ветка пройдена), но URL слишком длинный.
    message = _LinkMessage("https://example.com/" + "a" * 3000)
    bus = FakeBus()
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator())
    assert message.answers == ["URL слишком длинный"]
    assert bus.published == []
