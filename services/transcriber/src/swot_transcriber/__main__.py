"""Entry point for the transcriber service (wires DI via dishka)."""

import asyncio

from dishka import make_async_container
from swot_bus import RabbitBusProvider, RegistryProvider

from .providers import TranscriberAdaptersProvider, TranscribeServiceProvider
from .service import TranscribeService


async def _amain() -> None:
    container = make_async_container(
        RabbitBusProvider("video.transcribe", ["video.downloaded"]),
        RegistryProvider(),
        TranscriberAdaptersProvider(),
        TranscribeServiceProvider(),
    )
    try:
        async with container() as request_container:
            service = await request_container.get(TranscribeService)
            await service.run()
    finally:
        await container.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
