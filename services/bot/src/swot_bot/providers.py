"""Dishka providers for the bot service."""

from collections.abc import AsyncIterable

import aiogram
from aiogram import Router
from dishka import Provider, Scope, provide
from swot_contracts import AnalysisReady, JobFailed, MessageBus, Settings

from .handlers import ResultReporter, build_router
from .renderer import MessageRenderer
from .validation import UrlValidator


class BotHandlersProvider(Provider):
    """Provide bot, router, validator, renderer, reporter."""

    @provide(scope=Scope.APP)
    def bot(self, settings: Settings) -> aiogram.Bot:
        return aiogram.Bot(token=settings.bot_token or "")

    @provide(scope=Scope.APP)
    def router(self, settings: Settings) -> Router:
        router, _ = build_router(settings.admin_id)
        return router

    @provide(scope=Scope.APP)
    def validator(self, settings: Settings) -> UrlValidator:
        return UrlValidator(allowed_hosts=settings.allowed_sources)

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
            target_chat_id=settings.target_chat_id or 0,
            renderer=renderer,
            artifacts_dir=settings.artifacts_dir,
        )


class BotConsumer:
    """Long-running consumer: forwards analysis/job events to the reporter."""

    def __init__(self, bus: MessageBus, reporter: ResultReporter) -> None:
        self._bus = bus
        self._reporter = reporter

    async def __call__(self) -> None:
        async def handler(message) -> None:
            if isinstance(message, AnalysisReady):
                await self._reporter.on_analysis(message)
            elif isinstance(message, JobFailed):
                await self._reporter.on_failed(message)

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
