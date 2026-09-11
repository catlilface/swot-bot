"""normalize_tag и TagGate: тег предмета после ссылки (нормализация, FIFO)."""

from uuid import UUID

import pytest
from swot_bot.tags import TagGate, normalize_tag


def _tid(i: int) -> UUID:
    return UUID(int=i)


# --- normalize_tag -----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Высшая Математика", "высшая_математика"),
        ("  Физика\t 2 \n", "физика_2"),
        ("Информатика", "информатика"),
        ("C++\tProgramming", "c++_programming"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_tag(raw: str, expected: str) -> None:
    assert normalize_tag(raw) == expected


# --- TagGate -----------------------------------------------------------------


def test_tag_gate_empty_by_default() -> None:
    gate = TagGate()
    assert gate.pop_next() is None
    assert gate.tag_for(_tid(1)) is None


def test_tag_gate_fifo_order() -> None:
    gate = TagGate()
    a, b, c = _tid(1), _tid(2), _tid(3)
    gate.await_tag(a)
    gate.await_tag(b)
    gate.await_tag(c)
    assert gate.pop_next() == a
    assert gate.pop_next() == b
    assert gate.pop_next() == c
    assert gate.pop_next() is None


def test_tag_gate_put_get_forget() -> None:
    gate = TagGate()
    a = _tid(7)
    gate.put_tag(a, "высшая_математика")
    assert gate.tag_for(a) == "высшая_математика"
    gate.forget(a)
    assert gate.tag_for(a) is None


def test_tag_gate_put_tag_does_not_clear_pending() -> None:
    """put_tag только хранит тег; из ожидания задача уходит через pop_next.

    В хендлере порядок обратный: сначала ``pop_next`` (в handle_link), потом
    ``put_tag`` (в _accept_tag) — поэтому в норме put_tag получает задачу
    уже без pending. forget() снимает и то, и другое (доставка/сбой).
    """
    gate = TagGate()
    a = _tid(9)
    gate.await_tag(a)
    gate.put_tag(a, "физика")
    assert gate.tag_for(a) == "физика"
    assert gate.pop_next() == a  # pop_next снимает задачу из ожидания
    assert gate.pop_next() is None
    gate.forget(a)
    assert gate.tag_for(a) is None
