"""Unit tests for the downloader service using FakeBus + dishka container."""

from pathlib import Path
from uuid import UUID

from dishka import make_async_container
from swot_bus import FakeBus, InMemoryJobRegistry, RegistryProvider
from swot_contracts import DownloadRequest, JobStatus, SourceRef
from swot_downloader.domain import DownloadedMedia
from swot_downloader.service import DownloaderService


class StubRouter:
    """Synchronous stand-in for SourceRouter that writes a fake file."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp = tmp_path

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        dst_dir.mkdir(parents=True, exist_ok=True)
        f = dst_dir / "media.m4a"
        f.write_bytes(b"fake-audio")
        return DownloadedMedia(
            media_path=f, title="stub", duration_sec=60, resource_id="r1"
        )


async def test_downloader_publishes_video_downloaded(tmp_path: Path) -> None:
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    task_id = UUID("00000000-0000-0000-0000-000000000001")
    svc = DownloaderService(
        media_dir=str(tmp_path),
        router=StubRouter(tmp_path),  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
    )
    await svc.handle(
        DownloadRequest(
            task_id=task_id,
            trace_id="t-1",
            source=SourceRef(url="https://disk.yandex.ru/i/abc", kind="yandex_disk"),
        )
    )
    assert bus.published_types()[0].value == "video.downloaded"
    assert await registry.get_status(task_id) == JobStatus.READY


async def test_dishka_fake_container_resolves_services() -> None:
    stubs_holder: list[object] = []
    container = make_async_container(
        RegistryProvider(),
    )
    async with container() as request_container:
        registry = await request_container.get(InMemoryJobRegistry)
        stubs_holder.append(registry)
    await container.close()
    assert stubs_holder, "expected registry from dishka"
