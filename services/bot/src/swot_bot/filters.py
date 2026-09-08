"""Admin-only guard: only ADMIN_ID may submit links."""

from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsAdmin(BaseFilter):
    """Pass only if the sender is the configured admin."""

    def __init__(self, admin_id: int | None) -> None:
        self._admin_id = admin_id

    async def __call__(self, message: Message) -> bool:
        if self._admin_id is None:
            return False
        user = message.from_user
        if user is None:
            return False
        return user.id == self._admin_id
