"""Entry point for the analyzer service (wires DI via dishka)."""

import asyncio

from dishka import make_async_container
from swot_bus import RabbitBusProvider, RegistryProvider
from swot_observability import HealthServer, ObservabilityProvider, configure_logging

from .providers import AnalyzerAdaptersProvider, AnalyzeServiceProvider
from .service import AnalyzeService


async def _amain() -> None:
    configure_logging()
    container = make_async_container(
        RabbitBusProvider("video.analyze", ["transcript.ready"]),
        RegistryProvider(),
        ObservabilityProvider(),
        AnalyzerAdaptersProvider(),
        AnalyzeServiceProvider(),
    )
    try:
        async with container() as request_container:
            health: HealthServer = await request_container.get(HealthServer)
            health_task = asyncio.create_task(health.start())
            service = await request_container.get(AnalyzeService)
            await service.run()
            health_task.cancel()
    finally:
        await container.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
