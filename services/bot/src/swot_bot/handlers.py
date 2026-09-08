"""Telegram message handlers with dishka-injected dependencies."""

import logging
from pathlib import Path
from uuid import uuid4

from aiogram import F, Router
from aiogram.types import BufferedInputFile, Message
from dishka import FromDishka
from swot_contracts import (
    AnalysisReady,
    DownloadRequest,
    JobFailed,
    MessageBus,
    SourceRef,
)
from swot_contracts.ports import JobRegistry

from .filters import IsAdmin
from .renderer import MessageRenderer
from .validation import UrlValidator

logger = logging.getLogger(__name__)


def build_router(admin_id: int | None) -> tuple[Router, IsAdmin]:
    """Create a router bound to the admin filter (admin_id from config)."""
    admin_filter = IsAdmin(admin_id)
    router = Router(name="main")

    @router.message(admin_filter, F.text)
    async def handle_link(
        message: Message,
        bus: FromDishka[MessageBus],
        registry: FromDishka[JobRegistry],
        validator: FromDishka[UrlValidator],
    ) -> None:
        user_text = (message.text or "").strip()
        if not user_text.startswith(("http://", "https://")):
            await message.answer("Пришли ссылку на лекцию.")
            return
        try:
            url = validator.validate(user_text)
        except ValueError as exc:
            await message.answer(str(exc))
            return

        task_id = uuid4()
        await registry.create(task_id, url)
        await bus.publish(
            DownloadRequest(
                task_id=task_id,
                trace_id=f"t-{task_id.hex[:12]}",
                source=SourceRef(url=url, kind=_kind_for(url)),
            )
        )
        await message.answer("✅ Задача принята, обрабатываю…")

    return router, admin_filter


class ResultReporter:
    """Consumes analysis.ready / job.failed and posts to the target chat."""

    def __init__(
        self,
        bot,
        target_chat_id: int,
        renderer: MessageRenderer,
        artifacts_dir: str,
    ) -> None:
        self._bot = bot
        self._target = target_chat_id
        self._renderer = renderer
        self._artifacts = Path(artifacts_dir)

    async def on_analysis(self, msg: AnalysisReady) -> None:
        text = self._renderer.render(Path(msg.summary_path))
        await self._bot.send_message(self._target, text, parse_mode="HTML")

        srt = self._artifacts / str(msg.task_id) / "transcript.srt"
        if srt.exists():
            await self._bot.send_document(
                self._target,
                BufferedInputFile(srt.read_bytes(), filename="transcript.srt"),
            )

    async def on_failed(self, msg: JobFailed) -> None:
        await self._bot.send_message(self._target, f"⚠️ Задача упала: {msg.error}")


def _kind_for(url: str) -> str:
    host = url.split("/")[2].lower() if "://" in url else ""
    if "disk.yandex" in host:
        return "yandex_disk"
    if "drive.google" in host:
        return "gdrive"
    if "vkvideo" in host or "vk.com" in host:
        return "vkvideo"
    if "rutube" in host:
        return "rutube"
    return "generic"
