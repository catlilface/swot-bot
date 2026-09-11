"""Dishka providers for the bot service."""

from collections.abc import AsyncIterable

import aiogram
from aiogram import Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from dishka import Provider, Scope, provide
from swot_contracts import (
    AnalysisReady,
    BaseMessage,
    JobFailed,
    JobProgress,
    MessageBus,
    Settings,
)

from .handlers import ResultReporter, build_router
from .renderer import MessageRenderer
from .validation import UrlValidator


class BotHandlersProvider(Provider):
    """Provide bot, router, validator, renderer, reporter."""

    @provide(scope=Scope.APP)
    def bot(self, settings: Settings) -> aiogram.Bot:
        token = settings.telegram.token or ""
        base_url = (settings.telegram.api_base_url or "").strip().rstrip("/")
        if not base_url:
            return aiogram.Bot(token=token)
        # Dev stub / local proxy: point the client at a custom Bot API server.
        api = TelegramAPIServer(
            base=f"{base_url}/bot{{token}}/{{method}}",
            file=f"{base_url}/file/bot{{token}}/{{path}}",
        )
        return aiogram.Bot(token=token, session=AiohttpSession(api=api))

    @provide(scope=Scope.APP)
    def router(self, settings: Settings) -> Router:
        router, _ = build_router(settings.telegram.admin_id)
        return router

    @provide(scope=Scope.APP)
    def validator(self, settings: Settings) -> UrlValidator:
        return UrlValidator(allowed_hosts=settings.downloader.allowed_sources)

    @provide(scope=Scope.APP)
    def renderer(self) -> MessageRenderer:
        return MessageRenderer()

    @provide(scope=Scope.APP)
    def reporter(
        self,
        bot: aiogram.Bot,
        settings: Settings,
        renderer: MessageRenderer,
    ) -> ResultReporter:
        return ResultReporter(
            bot=bot,
            target_chat_id=settings.telegram.target_chat_id or 0,
            renderer=renderer,
            artifacts_dir=settings.artifacts_dir,
        )


class BotConsumer:
    """Long-running consumer: forwards analysis and job results to the reporter."""

    def __init__(self, bus: MessageBus, reporter: ResultReporter) -> None:
        self._bus = bus
        self._reporter = reporter

    async def __call__(self) -> None:
        async def handler(message: BaseMessage) -> None:
            if isinstance(message, AnalysisReady):
                await self._reporter.on_analysis(message)
            elif isinstance(message, JobFailed):
                await self._reporter.on_failed(message)
            elif isinstance(message, JobProgress):
                await self._reporter.on_progress(message)

        await self._bus.consume(handler)


class BotConsumersProvider(Provider):
    """Provide the result/job consumer that triggers the reporter."""

    @provide(scope=Scope.APP)
    async def consumer(
        self,
        bus: MessageBus,
        reporter: ResultReporter,
    ) -> AsyncIterable[BotConsumer]:
        yield BotConsumer(bus, reporter)
