"""Entry point for the analyzer service (wires DI via dishka)."""

import asyncio

from dishka import make_async_container
from swot_bus import RabbitBusProvider, RegistryProvider

from .providers import AnalyzerAdaptersProvider, AnalyzeServiceProvider
from .service import AnalyzeService


async def _amain() -> None:
    container = make_async_container(
        RabbitBusProvider("video.analyze", ["transcript.ready"]),
        RegistryProvider(),
        AnalyzerAdaptersProvider(),
        AnalyzeServiceProvider(),
    )
    try:
        async with container() as request_container:
            service = await request_container.get(AnalyzeService)
            await service.run()
    finally:
        await container.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
