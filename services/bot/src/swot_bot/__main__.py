"""Entry point for the bot service (wires aiogram + dishka)."""

import asyncio

import aiogram
from aiogram import Dispatcher, Router
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka
from swot_bus import RabbitBusProvider, RegistryProvider
from swot_observability import (
    HealthServer,
    ObservabilityProvider,
    SettingsProvider,
    configure_logging,
    run_until_shutdown,
)

from .providers import (
    BotConsumer,
    BotConsumersProvider,
    BotHandlersProvider,
)


async def _amain() -> None:
    configure_logging()
    container = make_async_container(
        RabbitBusProvider(
            "result.deliver", ["analysis.ready", "job.failed", "job.progress"]
        ),
        SettingsProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        BotHandlersProvider(),
        BotConsumersProvider(),
    )
    health: HealthServer | None = None
    try:
        async with container() as request_container:
            bot = await request_container.get(aiogram.Bot)
            router = await request_container.get(Router)
            consumer = await request_container.get(BotConsumer)
            health = await request_container.get(HealthServer)

            dp = Dispatcher()
            dp.include_router(router)
            setup_dishka(container=container, router=dp, auto_inject=True)

            await health.start()
            # T-1.6: SIGTERM/SIGINT → stop polling/consuming (aiogram's own
            # finally closes the bot session; drain in-flight in bus.close
            # below) → close bus → stop health server → exit 0.
            await run_until_shutdown(
                dp.start_polling(bot, handle_signals=False),
                consumer(),
            )
            await bot.session.close()
    finally:
        await container.close()
        if health is not None:
            await health.stop()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
