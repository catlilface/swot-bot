"""Telegram message handlers with dishka-injected dependencies."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog.contextvars
from aiogram import F, Router
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
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

#: Telegram-ошибки, которые стоит ретраить перед nack (T-1.7): сетевые,
#: flood control (429) и 5xx.
_RETRYABLE = (TelegramNetworkError, TelegramRetryAfter, TelegramServerError)
_DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)


def _chunk_text(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    """Split text to fit Telegram's 4096-char limit without losing any char.

    Cuts prefer newlines and never fall inside an HTML tag or ``&entity;``
    (T-1.7): ``"".join(chunks) == text`` и ни один чанк не содержит половинки
    тега/сущности.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        nl = rest.rfind("\n", 0, limit)
        cut = nl + 1 if nl > 0 else limit  # перенос остаётся в текущем чанке
        cut = _safe_tag_cut(rest, cut)
        if cut <= 0:  # весь префикс — один тег: без бесконечного цикла
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    chunks.append(rest)
    return chunks


def _safe_tag_cut(text: str, cut: int) -> int:
    """Move ``cut`` back if it falls inside an HTML tag or entity."""
    lt, gt = text[:cut].rfind("<"), text[:cut].rfind(">")
    if lt > gt:  # незакрытый тег в префиксе
        cut = _cut_before(text, lt)
    amp, semi = text[:cut].rfind("&"), text[:cut].rfind(";")
    if amp > semi and not any(ch.isspace() for ch in text[amp:cut]):
        cut = _cut_before(text, amp)  # разрез внутри &entity;
    return cut


def _cut_before(text: str, pos: int) -> int:
    """Cut before ``pos``, preferring the previous newline (keeping it)."""
    nl = text.rfind("\n", 0, pos)
    return nl + 1 if nl > 0 else pos


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
    """Consumes analysis.ready / job.failed and posts to the target chat.

    Transient Telegram errors (network / 429 / 5xx) are retried with backoff
    *before* the bus gets a chance to nack the message (T-1.7); once the
    retry budget is exhausted the error propagates to the bus, whose own
    retry/DLQ policy takes over.
    """

    def __init__(
        self,
        bot,
        target_chat_id: int,
        renderer: MessageRenderer,
        artifacts_dir: str,
        retry_delays: Sequence[float] = _DEFAULT_RETRY_DELAYS,
    ) -> None:
        self._bot = bot
        self._target = target_chat_id
        self._renderer = renderer
        self._artifacts = Path(artifacts_dir)
        self._retry_delays = tuple(retry_delays)

    async def _send(self, send: Callable[[], Awaitable[Any]], *, what: str) -> None:
        """Run a Telegram call, retrying transient errors with backoff.

        ``TelegramRetryAfter.retry_after`` is honored as the minimum delay.
        Non-retryable errors (e.g. 400 bad request) propagate immediately.
        """
        for attempt in range(len(self._retry_delays) + 1):
            try:
                await send()
                return
            except _RETRYABLE as exc:
                if attempt >= len(self._retry_delays):
                    logger.error(
                        "telegram send failed after %d retries: %s error=%s",
                        len(self._retry_delays),
                        what,
                        exc,
                    )
                    raise
                delay = self._retry_delays[attempt]
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    delay = max(delay, float(retry_after))
                logger.warning(
                    "telegram transient error, retry %d/%d in %.1fs: %s error=%s",
                    attempt + 1,
                    len(self._retry_delays),
                    delay,
                    what,
                    exc,
                )
                await asyncio.sleep(delay)

    async def _notify_delivery_failed(self, msg: AnalysisReady) -> None:
        """One normalized, human-readable notice — never a traceback (T-1.7)."""
        await self._send(
            lambda: self._bot.send_message(
                self._target,
                "⚠️ Не удалось доставить результат задачи "
                f"{msg.task_id} — подробности в логах бота.",
            ),
            what="delivery-failed notice",
        )

    async def _deliver_result(self, msg: AnalysisReady) -> None:
        """Render and send the result; any failure raises (see on_analysis)."""
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
            raise

        text = self._renderer.render(summary)
        for part in _chunk_text(text):
            await self._send(
                lambda p=part: self._bot.send_message(
                    self._target, p, parse_mode="HTML"
                ),
                what="result message",
            )

        srt = self._artifacts / str(msg.task_id) / "transcript.srt"
        try:
            srt = resolve_under(self._artifacts, srt)
        except ValueError as exc:
            # Non-fatal: the text is already delivered; skip the file.
            logger.error(
                "refusing srt outside artifacts: task_id=%s error=%s",
                msg.task_id,
                exc,
            )
            return
        if srt.exists():
            await self._send(
                lambda: self._bot.send_document(
                    self._target,
                    BufferedInputFile(srt.read_bytes(), filename="transcript.srt"),
                ),
                what="srt document",
            )

    async def on_analysis(self, msg: AnalysisReady) -> None:
        structlog.contextvars.bind_contextvars(
            task_id=str(msg.task_id), trace_id=msg.trace_id, stage="delivery"
        )
        try:
            try:
                await self._deliver_result(msg)
            except Exception:  # noqa: BLE001 - any delivery failure gets a notice
                # Technical details (incl. the traceback) stay in the log; the
                # admin gets one normalized, human-readable note (T-1.7).
                logger.exception(
                    "delivery failed: task_id=%s trace_id=%s",
                    msg.task_id,
                    msg.trace_id,
                )
                await self._notify_delivery_failed(msg)
                # If the notice could not be sent either (Telegram itself is
                # down), the exception propagates and the bus nacks the
                # message so its retry/DLQ policy takes over.
                return
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
            # Human-readable notice in the chat; the raw error string (often a
            # technical detail) stays in the log only (T-1.7).
            await self._send(
                lambda: self._bot.send_message(
                    self._target,
                    f"⚠️ Задача {msg.task_id} не завершилась успешно "
                    f"(стадия: {msg.stage}) — подробности в логах.",
                ),
                what="failure notice",
            )
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
            await self._send(
                lambda: self._bot.send_message(self._target, f"{label} ({msg.stage})"),
                what="progress message",
            )
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
