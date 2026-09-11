"""T-1.3: download speed/blocking (P1-3, P1-4) — adapter-level tests.

Covers the acceptance behaviors:
1. slow resource (> timeout_sec) → DownloadError → JobFailed, event loop
   keeps running (healthz-style ticks continue during the "download");
2. metadata duration > max_duration_sec → error **before** the download;
3. direct HTTP: text/html response on a .mp4 URL → error, file not saved;
4. file > max_file_mb → aborted, partial file removed.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from fakes import FakeBus
from swot_bus import InMemoryJobRegistry
from swot_contracts import DownloadRequest, JobStatus, SourceRef
from swot_downloader.adapters import (
    DirectHttpAdapter,
    SourceRouter,
    YtDlpAdapter,
)
from swot_downloader.domain import DownloadError
from swot_downloader.service import DownloaderService

# ---------------------------------------------------------------- YtDlpAdapter


async def test_slow_download_times_out_and_loop_stays_free(
    tmp_path: Path,
) -> None:
    """Slow (> timeout_sec) resource → DownloadError → JobFailed, not a hang;
    a healthz-style ticker keeps ticking while the "download" runs in a
    worker thread (the event loop is not blocked, P1-3)."""
    calls: list[bool] = []

    def slow_run(opts: dict, url: str, download: bool) -> dict:
        calls.append(download)
        time.sleep(0.3)  # simulates a slow host; runs in a worker thread
        return {"title": "x", "duration": 10}

    adapter = YtDlpAdapter(timeout_sec=0.1, run=slow_run)
    bus = FakeBus()
    registry = InMemoryJobRegistry()
    router = SourceRouter(
        yt_dlp=adapter,
        direct=DirectHttpAdapter(),
        probe=lambda url: _no_probe(url),  # type: ignore[arg-type]
    )
    svc = DownloaderService(
        media_dir=str(tmp_path),
        router=router,
        bus=bus,
        registry=registry,
    )
    task_id = UUID("00000000-0000-0000-0000-0000000000a1")
    req = DownloadRequest(
        task_id=task_id,
        trace_id="t-slow",
        source=SourceRef(url="https://slow.example.com/lecture", kind="generic"),
    )

    ticks = 0

    async def healthz_ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(healthz_ticker())
    try:
        await svc.handle(req)  # must finish (not hang) and publish JobFailed
    finally:
        ticker.cancel()

    # The event loop must have kept ticking during the timed-out download.
    assert ticks >= 3, "event loop was blocked during the download"
    # JobFailed(stage="download") was published, not a hang.
    types = [t.value for t in bus.published_types()]
    assert "job.failed" in types
    failed = next(m for m in bus.published if m.msg_type.value == "job.failed")
    assert failed.stage == "download"
    assert "timed out" in failed.error
    assert await registry.get_status(task_id) == JobStatus.FAILED
    # Only the metadata phase started; the download phase was never awaited
    # to completion within the deadline.
    assert calls == [False]


async def _no_probe(url: str) -> None:
    return None


async def test_duration_rejected_before_download(tmp_path: Path) -> None:
    """info duration > max_duration_sec → error **before** any download."""
    calls: list[bool] = []

    def fake_run(opts: dict, url: str, download: bool) -> dict:
        calls.append(download)
        return {"title": "x", "duration": 7200}

    adapter = YtDlpAdapter(timeout_sec=300, max_duration_sec=3600, run=fake_run)
    with pytest.raises(DownloadError, match="too long"):
        await adapter.download(
            SourceRef(url="https://youtube.com/watch?v=x", kind="generic"),
            tmp_path / "m",
        )
    # metadata extraction only; the download phase was never started
    assert calls == [False]


async def test_ytdlp_happy_path_audio_format(tmp_path: Path) -> None:
    """Normal flow: metadata → duration check → download; audio-first
    format is passed to yt-dlp (with a bounded video fallback for sources
    without an audio-only format, e.g. Yandex Disk); metadata lands in
    DownloadedMedia."""
    calls: list[bool] = []
    seen_opts: list[dict] = []

    def fake_run(opts: dict, url: str, download: bool) -> dict:
        calls.append(download)
        seen_opts.append(dict(opts))
        if download:
            target = Path(opts["outtmpl"].replace("%(ext)s", "m4a"))
            target.write_bytes(b"fake-audio-bytes")
        return {"title": "Lecture 1", "duration": 600, "id": "res-1"}

    adapter = YtDlpAdapter(timeout_sec=300, max_duration_sec=7200, run=fake_run)
    media = await adapter.download(
        SourceRef(url="https://youtube.com/watch?v=x", kind="generic"),
        tmp_path / "m",
    )
    # metadata phase first, then download
    assert calls == [False, True]
    # audio-first: чистый аудио пробуется первым; фолбэк на низкое видео
    # (<=480p) — для источников без аудио-трека (Yandex Disk отдаёт только video).
    assert all(
        o["format"].startswith("bestaudio[acodec=none]/bestaudio/")
        for o in seen_opts
    )
    assert media.title == "Lecture 1"
    assert media.duration_sec == 600
    assert media.resource_id == "res-1"
    assert media.media_path.name == "media.m4a"


# ---------------------------------------------------------- DirectHttpAdapter


async def _http_client(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> TestClient:
    app = web.Application()
    app.router.add_get("/lecture.mp4", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_direct_http_text_html_rejected_no_file(tmp_path: Path) -> None:
    """HTTP 200 with text/html on a .mp4 URL → error, file is NOT saved."""

    async def html_page(request: web.Request) -> web.StreamResponse:
        return web.Response(
            text="<html><body>Not Found</body></html>",
            content_type="text/html",
        )

    client = await _http_client(html_page)
    try:
        adapter = DirectHttpAdapter()
        url = str(client.make_url("/lecture.mp4"))
        dst = tmp_path / "m"
        with pytest.raises(DownloadError, match="content-type"):
            await adapter.download(SourceRef(url=url, kind="direct_http"), dst)
        assert not any(dst.iterdir()), "partial file must not be left behind"
    finally:
        await client.close()


async def test_direct_http_oversize_aborted_partial_removed(
    tmp_path: Path,
) -> None:
    """File > max_file_mb → aborted, the partial file is removed."""

    async def big_mp4(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, headers={"Content-Type": "video/mp4"})
        await resp.prepare(request)
        chunk = b"m" * 65536
        for _ in range(24):  # 1.5 MB total
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    client = await _http_client(big_mp4)
    try:
        adapter = DirectHttpAdapter(timeout_sec=30, max_file_mb=1)
        url = str(client.make_url("/lecture.mp4"))
        dst = tmp_path / "m"
        with pytest.raises(DownloadError, match="too large"):
            await adapter.download(SourceRef(url=url, kind="direct_http"), dst)
        assert not any(dst.iterdir()), "partial file must be removed"
    finally:
        await client.close()


async def test_direct_http_success_saves_file(tmp_path: Path) -> None:
    """A real media response (video/mp4) is saved as media.mp4."""

    body = b"m" * 4096

    async def small_mp4(request: web.Request) -> web.StreamResponse:
        return web.Response(body=body, content_type="video/mp4")

    client = await _http_client(small_mp4)
    try:
        adapter = DirectHttpAdapter()
        url = str(client.make_url("/lecture.mp4"))
        media = await adapter.download(
            SourceRef(url=url, kind="direct_http"), tmp_path / "m"
        )
        assert media.media_path.name == "media.mp4"
        assert media.media_path.read_bytes() == body
    finally:
        await client.close()
