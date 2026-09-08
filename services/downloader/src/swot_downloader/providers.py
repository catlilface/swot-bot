"""Dishka providers for the downloader service."""

from collections.abc import AsyncIterable

from dishka import Provider, Scope, provide
from swot_contracts import MessageBus, Settings
from swot_contracts.ports import JobRegistry

from .adapters import DirectHttpAdapter, SourceRouter, YtDlpAdapter
from .service import DownloaderService


class DownloaderAdaptersProvider(Provider):
    """Concrete per-source adapter wiring (Swappable media downloaders)."""

    @provide(scope=Scope.APP)
    def yt_dlp(self) -> YtDlpAdapter:
        return YtDlpAdapter()

    @provide(scope=Scope.APP)
    def direct(self) -> DirectHttpAdapter:
        return DirectHttpAdapter()

    @provide(scope=Scope.APP)
    def router(self, yt_dlp: YtDlpAdapter, direct: DirectHttpAdapter) -> SourceRouter:
        return SourceRouter(yt_dlp, direct)


class DownloaderServiceProvider(Provider):
    """Provide the downloader application service."""

    @provide(scope=Scope.APP)
    async def service(
        self,
        settings: Settings,
        router: SourceRouter,
        bus: MessageBus,
        registry: JobRegistry,
    ) -> AsyncIterable[DownloaderService]:
        svc = DownloaderService(
            media_dir=settings.media_dir,
            router=router,
            bus=bus,
            registry=registry,
            max_duration_sec=settings.max_video_duration_sec,
            artifacts_dir=settings.artifacts_dir,
            retention_hours=settings.artifacts_retention_hours,
        )
        yield svc
