"""Entry point for the downloader service (wires DI via dishka)."""

import asyncio

from dishka import make_async_container
from swot_bus import BusProvider, RegistryProvider

from .providers import DownloaderAdaptersProvider, DownloaderServiceProvider
from .service import DownloaderService


async def _amain() -> None:
    container = make_async_container(
        BusProvider(),
        RegistryProvider(),
        DownloaderAdaptersProvider(),
        DownloaderServiceProvider(),
    )
    try:
        async with container() as request_container:
            service = await request_container.get(DownloaderService)
            await service.run()
    finally:
        await container.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
