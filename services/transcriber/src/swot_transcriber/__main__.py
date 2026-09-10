"""Entry point for the transcriber service (wires DI via dishka)."""

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

from .providers import TranscriberAdaptersProvider, TranscribeServiceProvider
from .service import TranscribeService


async def _amain() -> None:
    configure_logging()
    container = make_async_container(
        RabbitBusProvider("video.transcribe", ["video.downloaded"]),
        SettingsProvider(),
        RegistryProvider(),
        ObservabilityProvider(),
        TranscriberAdaptersProvider(),
        TranscribeServiceProvider(),
    )
    health: HealthServer | None = None
    try:
        async with container() as request_container:
            health = await request_container.get(HealthServer)
            await health.start()
            service = await request_container.get(TranscribeService)
            # T-1.6: SIGTERM/SIGINT → stop consuming (drain in-flight in
            # bus.close below) → close bus → stop health server → exit 0.
            await run_until_shutdown(service.run())
    finally:
        await container.close()
        if health is not None:
            await health.stop()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
