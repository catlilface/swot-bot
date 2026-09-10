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
    def yt_dlp(self, settings: Settings) -> YtDlpAdapter:
        return YtDlpAdapter(
            timeout_sec=settings.downloader.download_timeout_sec,
            max_duration_sec=settings.downloader.max_video_duration_sec,
        )

    @provide(scope=Scope.APP)
    def direct(self, settings: Settings) -> DirectHttpAdapter:
        return DirectHttpAdapter(
            timeout_sec=settings.downloader.download_timeout_sec,
            max_file_mb=settings.downloader.max_file_mb,
        )

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
            max_duration_sec=settings.downloader.max_video_duration_sec,
            artifacts_dir=settings.artifacts_dir,
            retention_hours=settings.downloader.retention_hours,
            job_timeout_hours=settings.downloader.job_timeout_hours,
            # T-2.5: тот же allowlist, что и у бота (DOWNLOADER__ALLOWED_SOURCES).
            allowed_sources=settings.downloader.allowed_sources,
        )
        yield svc
