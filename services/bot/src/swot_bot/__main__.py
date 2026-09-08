"""Entry point for the bot service (wires aiogram + dishka)."""

import asyncio
import logging

import aiogram
from aiogram import Dispatcher, Router
from dishka import make_async_container
from dishka.integrations.aiogram import setup_dishka
from swot_bus import RabbitBusProvider, RegistryProvider

from .providers import (
    BotConsumer,
    BotConsumersProvider,
    BotHandlersProvider,
)

logger = logging.getLogger(__name__)


async def _amain() -> None:
    container = make_async_container(
        RabbitBusProvider("result.deliver", ["analysis.ready", "job.failed"]),
        RegistryProvider(),
        BotHandlersProvider(),
        BotConsumersProvider(),
    )
    try:
        async with container() as request_container:
            bot = await request_container.get(aiogram.Bot)
            router = await request_container.get(Router)
            consumer = await request_container.get(BotConsumer)

            dp = Dispatcher()
            dp.include_router(router)
            setup_dishka(container=container, router=dp, auto_inject=True)

            await asyncio.gather(
                dp.start_polling(bot),
                consumer(),
            )
    finally:
        await container.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
