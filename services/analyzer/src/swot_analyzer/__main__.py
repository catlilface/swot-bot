"""Entry point for the analyzer service."""

import asyncio

from dishka import make_async_container
from swot_bus import RabbitBusProvider, RegistryProvider
from swot_observability import (
    HealthServer,
    ObservabilityProvider,
    SettingsProvider,
    configure_logging,
    run_until_shutdown,
)

from .providers import AnalyzerAdaptersProvider, AnalyzeServiceProvider
from .service import AnalyzeService


async def _amain() -> None:
    configure_logging()
    container = make_async_container(
        RabbitBusProvider("video.analyze", ["transcript.ready"]),
        SettingsProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        AnalyzerAdaptersProvider(),
        AnalyzeServiceProvider(),
    )
    health: HealthServer | None = None
    try:
        async with container() as request_container:
            health = await request_container.get(HealthServer)
            await health.start()
            service = await request_container.get(AnalyzeService)
            await run_until_shutdown(service.run())
    finally:
        await container.close()
        if health is not None:
            await health.stop()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
