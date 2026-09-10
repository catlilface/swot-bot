"""T-0.3: инверсия SourceRouter (P0-3) — тесты route() и YtDlpAdapter.

Роутинг: платформы (youtube/vk/rutube/drive/disk.yandex/…) → YtDlpAdapter;
прямые медиа-URL (расширение или HEAD Content-Type video/*|audio/*) →
DirectHttpAdapter; остальное → YtDlpAdapter (дефолт).
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from swot_contracts import SourceRef
from swot_downloader.adapters import (
    DirectHttpAdapter,
    SourceRouter,
    YtDlpAdapter,
)


def _ref(url: str) -> SourceRef:
    return SourceRef(url=url, kind="generic")


class RecordingProbe:
    """Fake HEAD-проба: фиксирует вызовы, возвращает заданный Content-Type."""

    def __init__(self, content_type: str | None) -> None:
        self.content_type = content_type
        self.calls: list[str] = []

    async def __call__(self, url: str) -> str | None:
        self.calls.append(url)
        return self.content_type


def make_router(
    content_type: str | None = None,
) -> tuple[SourceRouter, RecordingProbe]:
    probe = RecordingProbe(content_type)
    return SourceRouter(YtDlpAdapter(), DirectHttpAdapter(), probe=probe), probe


PLATFORM_URLS = [
    "https://youtube.com/watch?v=abc123",
    "https://www.youtube.com/watch?v=abc123",
    "https://m.youtube.com/watch?v=abc123",
    "https://youtu.be/abc123",
    "https://vk.com/video-123_456",
    "https://vkvideo.ru/video/123",
    "https://rutube.ru/video/123",
    "https://vimeo.com/123456",
    "https://dzen.ru/videos/123",
    "https://ok.ru/video/123",
    "https://tiktok.com/@user/video/123",
    "https://disk.yandex.ru/i/abc",
    "https://drive.google.com/file/d/abc/view",
]


@pytest.mark.parametrize("url", PLATFORM_URLS)
async def test_route_platform_hosts_use_ytdlp(url: str) -> None:
    router, probe = make_router()
    adapter = await router.route(_ref(url))
    assert type(adapter) is YtDlpAdapter
    assert probe.calls == []  # проба не нужна: хост уже решил


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video.mp4",
        "https://example.com/audio.m4a",
        "https://example.com/lecture.mp3",
        "https://example.com/track.wav",
        "https://example.com/recording.ogg",
        "https://example.com/sound.flac",
        "https://example.com/VIDEO.MP4",  # регистр расширения не важен
    ],
)
async def test_route_media_extension_uses_direct(url: str) -> None:
    router, probe = make_router()
    adapter = await router.route(_ref(url))
    assert type(adapter) is DirectHttpAdapter
    assert probe.calls == []  # проба не нужна: расширение уже решило


async def test_route_plain_page_defaults_to_ytdlp() -> None:
    """Страница без медиа-расширения (HEAD не медиа) → yt-dlp по дефолту."""
    router, probe = make_router(content_type=None)
    adapter = await router.route(_ref("https://example.com/watch?v=abc"))
    assert type(adapter) is YtDlpAdapter
    assert probe.calls == ["https://example.com/watch?v=abc"]


@pytest.mark.parametrize(
    "content_type",
    ["video/mp4", "audio/ogg", "video/mp4; codecs=avc1.42E01E"],
)
async def test_route_head_probe_media_uses_direct(content_type: str) -> None:
    router, _ = make_router(content_type=content_type)
    adapter = await router.route(_ref("https://cdn.example.com/stream"))
    assert type(adapter) is DirectHttpAdapter


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "application/octet-stream", "text/html; charset=utf-8"],
)
async def test_route_head_probe_non_media_defaults_to_ytdlp(
    content_type: str,
) -> None:
    router, _ = make_router(content_type=content_type)
    adapter = await router.route(_ref("https://cdn.example.com/stream"))
    assert type(adapter) is YtDlpAdapter


# ---------------------------------------------------------------------------
# Интеграционный тест: YtDlpAdapter скачивает медиа, а не HTML
# ---------------------------------------------------------------------------


class _MediaHandler(SimpleHTTPRequestHandler):
    """Static file server that serves .m4a with a proper audio Content-Type."""

    def guess_type(self, path: str) -> str:
        if path.endswith(".m4a"):
            return "audio/mp4"
        return super().guess_type(path)

    def log_message(self, *args: object) -> None:
        pass  # тишина в тестах


async def test_ytdlp_adapter_downloads_media_not_html(
    tmp_path: Path,
) -> None:
    """Короткий 'видео' URL (тестовый сервер, отдаёт .m4a) → файл с медиа."""
    yt_dlp = pytest.importorskip("yt_dlp")
    del yt_dlp

    served = tmp_path / "served"
    served.mkdir()
    payload = b"\x00\x00\x00 fake-m4a-bytes" * 64
    (served / "lecture.m4a").write_bytes(payload)

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_MediaHandler, directory=str(served)),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        adapter = YtDlpAdapter()
        media = await adapter.download(
            _ref(f"http://127.0.0.1:{port}/lecture.m4a"),
            tmp_path / "out",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert media.media_path.exists()
    content = media.media_path.read_bytes()
    assert content == payload  # скачали медиа, а не HTML-страницу
    assert not content.lstrip().startswith(b"<")
    assert media.title == "lecture"
