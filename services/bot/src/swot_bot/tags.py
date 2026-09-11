"""Subject tag: ask after a link, normalize, append to the delivered result.

Chat flow: admin sends a link -> the bot asks for the subject tag in the next
message; the admin's next text message is consumed as the tag. The tag is
normalized (lowercase, whitespace runs -> ``_``) and appended to the end of
the result message, e.g. ``Высшая Математика`` -> ``высшая_математика``.

State is in-memory per bot process: the bot is the only service that asks for
and consumes tags; a restart simply loses the pending state (admin bot).
"""

import re
from uuid import UUID

_WHITESPACE = re.compile(r"\s+")


def normalize_tag(raw: str) -> str:
    """Normalize a subject name: trim, lowercase, whitespace runs -> ``_``.

    «Высшая Математика» -> «высшая_математика»; blank input -> ``""``.
    """
    return _WHITESPACE.sub("_", raw.strip().lower())


class TagGate:
    """Track which tasks are waiting for a subject tag (FIFO by submission)."""

    def __init__(self) -> None:
        self._pending: dict[UUID, None] = {}
        self._tags: dict[UUID, str] = {}

    def await_tag(self, task_id: UUID) -> None:
        """Mark a freshly accepted task as waiting for its subject tag."""
        self._pending[task_id] = None

    def pop_next(self) -> UUID | None:
        """Return the oldest task still waiting for a tag (or ``None``)."""
        if not self._pending:
            return None
        task_id = next(iter(self._pending))
        del self._pending[task_id]
        return task_id

    def put_tag(self, task_id: UUID, tag: str) -> None:
        """Store the normalized tag accepted for a waiting task."""
        self._tags[task_id] = tag

    def tag_for(self, task_id: UUID) -> str | None:
        """Normalized tag for a task, if the admin already supplied one."""
        return self._tags.get(task_id)

    def forget(self, task_id: UUID) -> None:
        """Drop all tag state for a finished task (delivered or failed)."""
        self._pending.pop(task_id, None)
        self._tags.pop(task_id, None)
