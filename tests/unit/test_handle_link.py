"""Ветки валидации handle_link (T-2.7): невалидный ввод → publish не происходит.

Плюс флоу тегов: после ссылки бот просит тег предмета, следующее сообщение
админа потребляется как тег (нормализация) и попадает в конец результата.
"""

from uuid import UUID

import pytest
from fakes import FakeBus
from swot_bot.handlers import handle_link
from swot_bot.tags import TagGate
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
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), TagGate())
    assert message.answers == ["Пришли ссылку на лекцию."]
    assert bus.published == []


@pytest.mark.asyncio
async def test_handle_link_empty_text_replies_hint() -> None:
    message = _LinkMessage(None)
    bus = FakeBus()
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), TagGate())
    assert message.answers == ["Пришли ссылку на лекцию."]
    assert bus.published == []


@pytest.mark.asyncio
async def test_handle_link_invalid_url_replies_error_without_publish() -> None:
    # http(s)-префикс есть (первая ветка пройдена), но URL слишком длинный.
    message = _LinkMessage("https://example.com/" + "a" * 3000)
    bus = FakeBus()
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), TagGate())
    assert message.answers == ["URL слишком длинный"]
    assert bus.published == []


# --- Флоу тегов: после ссылки следующее сообщение — тег предмета ----------


async def _submit_link(
    bus: FakeBus, tags: TagGate, url: str = "https://example.com/lecture.mp4"
) -> UUID:
    """Прислать ссылку и вернуть task_id созданной задачи."""
    message = _LinkMessage(url)
    before = len(bus.published)
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert len(bus.published) == before + 1
    return bus.published[-1].task_id


@pytest.mark.asyncio
async def test_link_accepted_asks_for_tag() -> None:
    bus = FakeBus()
    tags = TagGate()
    message = _LinkMessage("https://example.com/lecture.mp4")
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert message.answers == [
        "Задача принята, обрабатываю…\n"
        "Теперь пришли тег с названием предмета "
        "(например: «Высшая Математика»)."
    ]
    task_id = bus.get(0).task_id
    assert tags.pop_next() == task_id  # задача ждёт тег


@pytest.mark.asyncio
async def test_next_message_consumed_as_normalized_tag() -> None:
    bus = FakeBus()
    tags = TagGate()
    task_id = await _submit_link(bus, tags)

    tag_msg = _LinkMessage("  Высшая   Математика ")
    await handle_link(tag_msg, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert tag_msg.answers == [
        "Тег «высшая_математика» принят — добавлю его в конец результата."
    ]
    assert tags.tag_for(task_id) == "высшая_математика"
    assert len(bus.published) == 1  # тег не публикует новые ивенты
    assert tags.pop_next() is None  # ожидающих тегов больше нет


@pytest.mark.asyncio
async def test_url_while_waiting_tag_reasks() -> None:
    bus = FakeBus()
    tags = TagGate()
    task_id = await _submit_link(bus, tags)

    tag_msg = _LinkMessage("https://example.com/another.mp4")
    await handle_link(tag_msg, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert tag_msg.answers == ["Сначала пришли тег с названием предмета (не ссылку)."]
    assert tags.pop_next() == task_id  # всё ещё ждёт тег
    assert len(bus.published) == 1


@pytest.mark.asyncio
async def test_blank_tag_reasks() -> None:
    bus = FakeBus()
    tags = TagGate()
    task_id = await _submit_link(bus, tags)

    tag_msg = _LinkMessage("   ")
    await handle_link(tag_msg, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert tag_msg.answers == ["Тег не может быть пустым — пришли название предмета."]
    assert tags.pop_next() == task_id


@pytest.mark.asyncio
async def test_plain_text_without_pending_task_asks_for_link() -> None:
    bus = FakeBus()
    tags = TagGate()
    message = _LinkMessage("привет")
    await handle_link(message, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert message.answers == ["Пришли ссылку на лекцию."]


@pytest.mark.asyncio
async def test_tags_assigned_in_order_for_sequential_links() -> None:
    """Ссылка → тег → ссылка → тег: тег всегда попадает в свою задачу."""
    bus = FakeBus()
    tags = TagGate()
    first = await _submit_link(bus, tags, "https://example.com/a.mp4")

    tag_msg = _LinkMessage("Физика")
    await handle_link(tag_msg, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert tags.tag_for(first) == "физика"
    assert tags.pop_next() is None  # ожидающих тегов больше нет

    second = await _submit_link(bus, tags, "https://example.com/b.mp4")
    tag_msg2 = _LinkMessage("Русский")
    await handle_link(tag_msg2, bus, InMemoryJobRegistry(), UrlValidator(), tags)
    assert tags.tag_for(first) == "физика"  # первый тег не перезаписан
    assert tags.tag_for(second) == "русский"
    assert tags.pop_next() is None
