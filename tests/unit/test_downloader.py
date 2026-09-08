"""Unit tests for the downloader service using FakeBus + dishka container."""

import os
import time
from pathlib import Path
from uuid import UUID

from dishka import make_async_container
from swot_bus import FakeBus, InMemoryJobRegistry, RegistryProvider
from swot_contracts import DownloadRequest, JobStatus, SourceRef
from swot_downloader.domain import DownloadedMedia
from swot_downloader.service import DownloaderService


async def test_artifacts_ttl_cleanup(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    old = artifacts / "old"
    fresh = artifacts / "fresh"
    old.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (old / "summary.json").write_text("{}", encoding="utf-8")
    (fresh / "summary.json").write_text("{}", encoding="utf-8")
    past = time.time() - 200 * 3600  # старше 7 дней
    os.utime(old, (past, past))
    svc = DownloaderService(
        media_dir=str(tmp_path),
        router=StubRouter(tmp_path),  # type: ignore[arg-type]
        bus=FakeBus(),  # type: ignore[arg-type]
        registry=InMemoryJobRegistry(),  # type: ignore[arg-type]
        artifacts_dir=str(artifacts),
        retention_hours=168,
    )
    await svc._cleanup_old_artifacts(168)
    assert not old.exists()
    assert fresh.exists()


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
    assert "video.downloaded" in [t.value for t in bus.published_types()]
    ready = bus.get(1)
    assert ready.msg_type.value == "video.downloaded"
    assert ready.media_path.endswith("media.m4a")
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
