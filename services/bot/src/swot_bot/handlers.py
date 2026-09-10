"""Telegram message handlers with dishka-injected dependencies."""

import logging
from pathlib import Path
from uuid import uuid4

import structlog.contextvars
from aiogram import F, Router
from aiogram.types import BufferedInputFile, Message
from dishka import FromDishka
from swot_contracts import (
    AnalysisReady,
    DownloadRequest,
    JobFailed,
    JobProgress,
    MessageBus,
    SourceRef,
    resolve_under,
)
from swot_contracts.ports import JobRegistry

from .filters import IsAdmin
from .renderer import MessageRenderer
from .validation import UrlValidator

logger = logging.getLogger(__name__)

TG_MSG_LIMIT = 4096


def _chunk_text(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    """Split long text to fit Telegram's 4096-char limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        cut = cut if cut > 0 else limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    chunks.append(rest)
    return chunks


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
        trace_id = f"t-{task_id.hex[:12]}"
        # Bind BEFORE the first publish so the swot-trace-id header and the
        # bot's own logs carry the run's context (T-1.5).
        structlog.contextvars.bind_contextvars(
            task_id=str(task_id), trace_id=trace_id, stage="new"
        )
        try:
            logger.info(
                "link received: task_id=%s trace_id=%s url=%s",
                task_id,
                trace_id,
                user_text,
            )
            await registry.create(task_id, url)
            await bus.publish(
                DownloadRequest(
                    task_id=task_id,
                    trace_id=trace_id,
                    source=SourceRef(url=url, kind=_kind_for(url)),
                )
            )
            await message.answer("✅ Задача принята, обрабатываю…")
        finally:
            structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")

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
        structlog.contextvars.bind_contextvars(
            task_id=str(msg.task_id), trace_id=msg.trace_id, stage="delivery"
        )
        try:
            try:
                # Path containment: summary_path comes from the analyzer message and
                # must stay inside the bot's own artifacts dir (P0-6).
                summary = resolve_under(self._artifacts, msg.summary_path)
            except ValueError as exc:
                logger.error(
                    "refusing summary outside artifacts: task_id=%s path=%s error=%s",
                    msg.task_id,
                    msg.summary_path,
                    exc,
                )
                return

            text = self._renderer.render(summary)
            for part in _chunk_text(text):
                await self._bot.send_message(self._target, part, parse_mode="HTML")

            srt = self._artifacts / str(msg.task_id) / "transcript.srt"
            try:
                srt = resolve_under(self._artifacts, srt)
            except ValueError as exc:
                logger.error(
                    "refusing srt outside artifacts: task_id=%s error=%s",
                    msg.task_id,
                    exc,
                )
                return
            if srt.exists():
                await self._bot.send_document(
                    self._target,
                    BufferedInputFile(srt.read_bytes(), filename="transcript.srt"),
                )
            # Last log of the run in this service: same trace/task as the
            # whole pipeline (T-1.5).
            logger.info(
                "result delivered: task_id=%s trace_id=%s",
                msg.task_id,
                msg.trace_id,
            )
        finally:
            structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")

    async def on_failed(self, msg: JobFailed) -> None:
        structlog.contextvars.bind_contextvars(
            task_id=str(msg.task_id), trace_id=msg.trace_id, stage=msg.stage
        )
        try:
            await self._bot.send_message(self._target, f"⚠️ Задача упала: {msg.error}")
            logger.info(
                "failure delivered: task_id=%s trace_id=%s stage=%s error=%s",
                msg.task_id,
                msg.trace_id,
                msg.stage,
                msg.error,
            )
        finally:
            structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")

    async def on_progress(self, msg: JobProgress) -> None:
        structlog.contextvars.bind_contextvars(
            task_id=str(msg.task_id), trace_id=msg.trace_id, stage=msg.stage
        )
        try:
            label = {
                "downloading": "⬇️ Скачиваю…",
                "transcribing": "📝 Транскрибирую…",
                "analyzing": "🧠 Анализирую…",
            }.get(msg.stage, msg.stage)
            await self._bot.send_message(self._target, f"{label} ({msg.stage})")
        finally:
            structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")


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
