"""Telegram message handlers with dishka-injected dependencies."""

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog.contextvars
from aiogram import Bot, F, Router
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
from .tags import TagGate, normalize_tag
from .validation import UrlValidator

logger = logging.getLogger(__name__)

TG_MSG_LIMIT = 4096

#: Telegram-ошибки, которые стоит ретраить перед nack (T-1.7): сетевые,
#: flood control (429) и 5xx.
_RETRYABLE = (TelegramNetworkError, TelegramRetryAfter, TelegramServerError)
_DEFAULT_RETRY_DELAYS = (1.0, 2.0, 4.0)

#: Content hashtags recognized in the lecture summary. The LLM may put
#: anything into summary.json "hashtags", so only these values (fixed
#: order) reach the result message.
_CONTENT_HASHTAGS = ("задания", "сессия")


def _content_hashtags(hashtags: Iterable[str]) -> str:
    """Recognized content hashtags for the result ("#задания #сессия").

    Unknown values (LLM noise) are dropped; case/whitespace differences in
    known values are tolerated.
    """
    normalized = {h.strip().lower() for h in hashtags}
    return " ".join(f"#{tag}" for tag in _CONTENT_HASHTAGS if tag in normalized)


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


async def handle_link(
    message: Message,
    bus: FromDishka[MessageBus],
    registry: FromDishka[JobRegistry],
    validator: FromDishka[UrlValidator],
    tags: FromDishka[TagGate],
) -> None:
    """Одиночное текстовое сообщение: тег для ожидающей задачи или ссылка.

    Если после последней ссылки ещё не прислан тег предмета, сообщение
    трактуется как тег (нормализуется и сохраняется к задаче). Иначе —
    как ссылка на видео. Module-level function (not nested in
    ``build_router``) so the branches are directly unit-testable (T-2.7).
    """
    user_text = (message.text or "").strip()

    pending_id = tags.pop_next()
    if pending_id is not None:
        await _accept_tag(message, tags, pending_id, user_text)
        return

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
        # Следующее сообщение админа — тег предмета (FIFO, см. TagGate).
        tags.await_tag(task_id)
        await message.answer(
            "Задача принята, обрабатываю…\n"
            "Теперь пришли тег с названием предмета (например: «Высшая Математика»)."
        )
    finally:
        structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")


async def _accept_tag(
    message: Message, tags: TagGate, pending_id: UUID, user_text: str
) -> None:
    """Попробовать обработать сообщение как тег предмета ожидающей задачи."""
    if user_text.startswith(("http://", "https://")):
        tags.await_tag(pending_id)
        await message.answer("Сначала пришли тег с названием предмета (не ссылку).")
        return
    tag = normalize_tag(user_text)
    if not tag:
        tags.await_tag(pending_id)
        await message.answer("Тег не может быть пустым — пришли название предмета.")
        return
    tags.put_tag(pending_id, tag)
    await message.answer(f"Тег «{tag}» принят — добавлю его в конец результата.")


def build_router(admin_id: int | None) -> tuple[Router, IsAdmin]:
    """Create a router bound to the admin filter (admin_id from config)."""
    admin_filter = IsAdmin(admin_id)
    router = Router(name="main")
    router.message.register(handle_link, admin_filter, F.text)
    return router, admin_filter


class ResultReporter:
    """Consumes analysis.ready / job.failed / job.progress.

    The target chat receives ONLY the finished summary (+ SRT document);
    everything else — progress updates, failure and delivery-failure
    notices — goes to the admin (``admin_id``).

    Transient Telegram errors (network / 429 / 5xx) are retried with backoff
    *before* the bus gets a chance to nack the message (T-1.7); once the
    retry budget is exhausted the error propagates to the bus, whose own
    retry/DLQ policy takes over.
    """

    def __init__(
        self,
        bot: Bot,
        target_chat_id: int,
        renderer: MessageRenderer,
        artifacts_dir: str,
        tags: TagGate,
        retry_delays: Sequence[float] = _DEFAULT_RETRY_DELAYS,
        target_topic_id: int | None = None,
        admin_id: int | None = None,
    ) -> None:
        self._bot = bot
        self._target = target_chat_id
        self._topic = target_topic_id
        self._renderer = renderer
        self._artifacts = Path(artifacts_dir)
        self._tags = tags
        self._retry_delays = tuple(retry_delays)
        self._admin = admin_id

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
        """One normalized, human-readable notice — never a traceback (T-1.7).

        Goes to the admin: the target chat receives only summaries and SRT.
        Without a configured admin the notice is dropped (the log keeps the
        details) instead of nacking into an endless retry loop.
        """
        admin = self._admin
        if admin is None:
            logger.warning(
                "admin not configured, delivery-failed notice dropped: task_id=%s",
                msg.task_id,
            )
            return
        await self._send(
            lambda: self._bot.send_message(
                admin,
                "Не удалось доставить результат задачи "
                f"{msg.task_id} — подробности в логах бота.",
            ),
            what="delivery-failed notice",
        )

    def _thread(self) -> dict[str, Any]:
        """Forum-topic routing: message_thread_id only when a topic is set.

        Empty topic → sends land in the target chat's default (General)
        topic, exactly as before the topic support was added.
        """
        return {"message_thread_id": self._topic} if self._topic is not None else {}

    def _resolve_artifact(self, path: str) -> Path | None:
        """Resolve a producer-provided path under the bot's artifacts dir.

        Containment (P0-6): the path must stay inside *artifacts_dir*.
        Returns ``None`` for empty, escaping, or non-file paths.
        T-2.4: paths come from the bus event, not from our own config.
        """
        if not path:
            return None
        try:
            resolved = resolve_under(self._artifacts, path)
        except ValueError as exc:
            logger.error(
                "refusing artifact path outside artifacts: path=%s error=%s",
                path,
                exc,
            )
            return None
        if not resolved.is_file():
            return None
        return resolved

    async def _deliver_result(self, msg: AnalysisReady) -> None:
        """Render and send the result; any failure raises (see on_analysis)."""
        summary = self._resolve_artifact(msg.summary_path)
        if summary is None:
            raise ValueError(f"summary path unavailable: {msg.summary_path!r}")

        text = self._renderer.render(summary)
        # Исходная ссылка админа — в конце результата (до строки с тегом);
        # экранируем под Telegram HTML-разметку (иначе & в URL ломает parse).
        if msg.source.url:
            text = f"{text.rstrip()}\n\n🔗 {html.escape(msg.source.url, quote=False)}"
        tag = self._tags.tag_for(msg.task_id)
        suffixes = []
        # Контент-хэштеги (#задания / #сессия) — по выжимке лекции, тег
        # предмета — последней позицией (собирается в handle_link).
        content = _content_hashtags(self._renderer.hashtags(summary))
        if content:
            suffixes.append(content)
        if tag:
            suffixes.append(f"#{tag}")
        if suffixes:
            text = f"{text.rstrip()}\n\n{' '.join(suffixes)}"
        for part in _chunk_text(text):

            async def send_part(text_part: str = part) -> None:
                await self._bot.send_message(
                    self._target, text_part, parse_mode="HTML", **self._thread()
                )

            await self._send(send_part, what="result message")

        # T-2.4: SRT-путь несёт ивент analysis.ready (из конфига транскрибера);
        # бот не пересобирает его из своего artifacts_dir/task_id.
        srt = self._resolve_artifact(msg.srt_path)
        if srt is not None:
            await self._send(
                lambda: self._bot.send_document(
                    self._target,
                    BufferedInputFile(srt.read_bytes(), filename="transcript.srt"),
                    **self._thread(),
                ),
                what="srt document",
            )
        # Задача доставлена: состояние тегов не нужно. Если доставка выше
        # упала (raise до этой строки) — тег не забыт и попадёт в сообщение
        # при redelivery ивента с шины.
        self._tags.forget(msg.task_id)
        # Транскрибация (SRT + segments.json) после доставки уже не нужна:
        # удаляем, чтобы не разрастать общий том (P1-11). Best-effort: ошибка
        # очистки не должна ломать уже успешную доставку.
        self._cleanup_transcript(msg, srt)

    def _cleanup_transcript(self, msg: AnalysisReady, srt: Path | None) -> None:
        """Delete SRT + segments.json after delivery (best-effort, P0-6).

        The SRT path comes from the event (already containment-checked in
        :meth:`_resolve_artifact`); segments.json lives next to it in the
        analyzer's base dir.
        """
        if srt is not None:
            self._unlink_quietly(srt)
        if not msg.base_dir:
            return
        try:
            base = resolve_under(self._artifacts, msg.base_dir)
        except ValueError as exc:
            logger.warning(
                "transcript cleanup refused (outside artifacts): path=%s error=%s",
                msg.base_dir,
                exc,
            )
            return
        self._unlink_quietly(base / "segments.json")

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        """Best-effort file removal: failures are logged, not raised."""
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("transcript cleanup failed: path=%s error=%s", path, exc)

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
            # Human-readable notice goes to the admin (the target chat gets
            # only summaries and SRT); the raw error string (often a
            # technical detail) stays in the log only (T-1.7).
            admin = self._admin
            if admin is None:
                logger.warning(
                    "admin not configured, failure notice dropped: task_id=%s stage=%s error=%s",
                    msg.task_id,
                    msg.stage,
                    msg.error,
                )
            else:
                await self._send(
                    lambda: self._bot.send_message(
                        admin,
                        f"Задача {msg.task_id} не завершилась успешно "
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
            self._tags.forget(msg.task_id)
        finally:
            structlog.contextvars.unbind_contextvars("task_id", "trace_id", "stage")

    async def on_progress(self, msg: JobProgress) -> None:
        structlog.contextvars.bind_contextvars(
            task_id=str(msg.task_id), trace_id=msg.trace_id, stage=msg.stage
        )
        try:
            # Progress goes to the admin: the target chat receives only the
            # finished summary and the SRT.
            admin = self._admin
            if admin is None:
                logger.warning(
                    "admin not configured, progress dropped: task_id=%s stage=%s",
                    msg.task_id,
                    msg.stage,
                )
                return
            label = {
                "downloading": "Скачиваю…",
                "transcribing": "Транскрибирую…",
                "analyzing": "Анализирую…",
            }.get(msg.stage, msg.stage)
            await self._send(
                lambda: self._bot.send_message(admin, f"{label} ({msg.stage})"),
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
