"""Per-source download adapters using yt-dlp and direct HTTP.

Routing: ``SourceRouter.route()`` returns the adapter to use for a URL:
platform hosts (yt-dlp knows their extractors) → ``YtDlpAdapter``; direct
media URLs (media extension, or ``Content-Type: video/*|audio/*`` per HEAD)
→ ``DirectHttpAdapter``; anything else → ``YtDlpAdapter`` (default).

Hardening (P1-3, P1-4): yt-dlp's blocking work runs in a worker thread
under an overall deadline (the event loop must never freeze), and video
duration is checked from metadata extraction **before** downloading;
direct HTTP validates that the response really is media (content-type)
and enforces a hard file-size limit with partial-file cleanup.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from swot_contracts import SourceRef

from .domain import DownloadedMedia, DownloadError

#: Hosts with dedicated yt-dlp extractors; the router sends them to
#: ``YtDlpAdapter`` (suffix match covers e.g. www./m. subdomains).
PLATFORM_HOSTS = frozenset(
    {
        "youtube.com",
        "youtu.be",
        "vk.com",
        "vkvideo.ru",
        "rutube.ru",
        "vimeo.com",
        "dzen.ru",
        "ok.ru",
        "tiktok.com",
        "disk.yandex.ru",
        "drive.google.com",
    }
)

#: Extensions of direct media files; the router sends them to
#: ``DirectHttpAdapter``.
MEDIA_EXTENSIONS = frozenset({".mp4", ".m4a", ".mp3", ".wav", ".ogg", ".flac"})

#: HEAD probe: url -> response Content-Type (or None on error/timeout).
HeadProbe = Callable[[str], Awaitable[str | None]]


async def _default_head_probe(url: str) -> str | None:
    """Best-effort HEAD probe: Content-Type header or None on any error."""
    import aiohttp

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            async with session.head(url) as resp:
                return resp.headers.get("content-type")
    except Exception:  # noqa: BLE001 — probe must never break routing
        return None


class YtDlpAdapter:
    """Generic media downloader via yt-dlp (Python API, not CLI).

    The blocking yt-dlp work runs in a worker thread under a deadline, so
    a slow/unreachable host cannot freeze the service's event loop
    (P1-3). Video duration is checked from metadata extraction **before**
    the download starts, and the format prefers codec-free audio (no
    video fallback).
    """

    #: Audio-first format: codec-free best audio, then best audio (no video).
    FORMAT = "bestaudio[acodec=none]/bestaudio"

    def __init__(
        self,
        timeout_sec: int = 300,
        max_duration_sec: int | None = None,
        run: Callable[[dict, str, bool], dict | None] | None = None,
    ) -> None:
        self._timeout = timeout_sec
        self._max_duration_sec = max_duration_sec
        # Injectable seam for tests (the default is a synchronous yt-dlp
        # call, executed in a worker thread).
        self._run = run or _yt_dlp_run

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        dst_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(dst_dir / "media.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": self.FORMAT,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        # Phase 1: metadata only — reject too-long videos before spending
        # download traffic.
        info = await self._run_with_timeout(opts, source.url, download=False)
        if not info:
            raise DownloadError(f"no metadata extracted for {source.url}")
        duration = int(info.get("duration") or 0)
        if self._max_duration_sec and duration > self._max_duration_sec:
            raise DownloadError(
                f"video too long: {duration}s (max {self._max_duration_sec}s)"
            )
        # Phase 2: the actual download, under the same deadline.
        await self._run_with_timeout(opts, source.url, download=True)
        path = _resolve_media(dst_dir)
        return DownloadedMedia(
            media_path=path,
            title=info.get("title") or source.url,
            duration_sec=duration,
            resource_id=str(info["id"]) if info.get("id") else None,
        )

    async def _run_with_timeout(
        self, opts: dict, url: str, *, download: bool
    ) -> dict | None:
        # to_thread keeps the event loop free while yt-dlp blocks; a slow
        # host must end in a timeout, not a service-wide hang. (The worker
        # thread cannot be cancelled — yt-dlp has no abort API — so on
        # timeout it keeps running in the background until it finishes.)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._run, opts, url, download),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise DownloadError(f"download timed out after {self._timeout}s") from exc


def _yt_dlp_run(
    opts: dict[str, Any], url: str, download: bool
) -> dict[str, Any] | None:
    """Synchronous yt-dlp call (runs in a worker thread, see YtDlpAdapter)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(url, download=download)
        return cast("dict[str, Any] | None", result)


class DirectHttpAdapter:
    """Download a direct media URL (mp4/webm/m4a etc.) via plain HTTP.

    Validates that the response really is media (content-type check, P1-4)
    and enforces a hard file-size limit; on any failure the partial file is
    removed so no garbage lingers in the task dir.
    """

    AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}

    #: Connect/read caps are separate from the overall total (P1-4): a slow
    #: big file must not be killed by the total, but a dead connection must.
    CONNECT_TIMEOUT_SEC = 15
    READ_TIMEOUT_SEC = 60  # no data for 60s -> abort

    def __init__(self, timeout_sec: int = 300, max_file_mb: int = 1024) -> None:
        self._timeout = timeout_sec
        self._max_file_mb = max_file_mb
        self._max_bytes = max_file_mb * 1024 * 1024

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        import aiohttp

        dst_dir.mkdir(parents=True, exist_ok=True)
        timeout = aiohttp.ClientTimeout(
            total=self._timeout,
            connect=self.CONNECT_TIMEOUT_SEC,
            sock_connect=self.CONNECT_TIMEOUT_SEC,
            sock_read=self.READ_TIMEOUT_SEC,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(source.url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                _validate_media_content_type(content_type, source.url)
                ext = _extension_from_content_type(content_type)
                if ext == "bin":  # unknown CT: keep the URL's media suffix
                    ext = _url_media_extension(source.url, ext)
                target = dst_dir / f"media.{ext}"
                written = 0
                try:
                    with target.open("wb") as fh:
                        async for chunk in resp.content.iter_chunked(2**16):
                            if written + len(chunk) > self._max_bytes:
                                raise DownloadError(
                                    f"file too large: >{self._max_file_mb} MB"
                                )
                            written += len(chunk)
                            fh.write(chunk)
                except Exception:
                    # No partial files on any failure.
                    target.unlink(missing_ok=True)
                    raise
        return DownloadedMedia(
            media_path=target,
            title=source.url,
            duration_sec=0,
            resource_id=None,
        )


def _validate_media_content_type(content_type: str, url: str) -> None:
    """Raise `DownloadError` for non-media responses (e.g. an HTML error
    page served with 200, P1-4)."""
    base = content_type.split(";", 1)[0].strip().lower()
    if not base or base.startswith(("video/", "audio/", "application/octet-stream")):
        return
    raise DownloadError(f"unexpected content-type {base!r} for {url!r}; expected media")


def _url_media_extension(url: str, fallback: str) -> str:
    """A known media extension from the URL path, else the fallback."""
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix.lstrip(".") if suffix in MEDIA_EXTENSIONS else fallback


class SourceRouter:
    """Choose an adapter based on the URL; ``route()`` returns the adapter.

    1. Platform hosts (``PLATFORM_HOSTS``) → ``YtDlpAdapter``;
    2. Direct media URL — extension in ``MEDIA_EXTENSIONS`` or HEAD
       ``Content-Type`` of ``video/*``/``audio/*`` → ``DirectHttpAdapter``;
    3. Everything else → ``YtDlpAdapter`` (yt-dlp is the default).
    """

    def __init__(
        self,
        yt_dlp: YtDlpAdapter,
        direct: DirectHttpAdapter,
        probe: HeadProbe | None = None,
    ) -> None:
        self._yt_dlp = yt_dlp
        self._direct = direct
        self._probe = probe or _default_head_probe

    async def route(self, source: SourceRef) -> YtDlpAdapter | DirectHttpAdapter:
        """Return the adapter that should handle ``source.url``."""
        url = source.url
        host = urlparse(url).netloc.lower()
        if ":" in host:  # explicit port, e.g. youtube.com:443
            host = host.split(":", 1)[0]
        if host and self._is_platform_host(host):
            return self._yt_dlp
        ext = Path(urlparse(url).path).suffix.lower()
        if ext in MEDIA_EXTENSIONS:
            return self._direct
        content_type = await self._probe(url)
        if content_type:
            content_type = content_type.split(";", 1)[0].strip().lower()
            if content_type.startswith(("video/", "audio/")):
                return self._direct
        return self._yt_dlp

    @staticmethod
    def _is_platform_host(host: str) -> bool:
        return any(
            host == domain or host.endswith("." + domain) for domain in PLATFORM_HOSTS
        )

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        adapter = await self.route(source)
        return await adapter.download(source, dst_dir)


def _resolve_media(dst_dir: Path) -> Path:
    media = list(dst_dir.iterdir())
    if not media:
        raise FileNotFoundError("no media produced by downloader")
    # prefer video/audio, else first
    media.sort(key=lambda p: p.stat().st_size, reverse=True)
    return media[0]


def _extension_from_content_type(ct: str) -> str:
    mapping = {
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/x-wav": "wav",
        "audio/ogg": "ogg",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
    }
    for key, ext in mapping.items():
        if key in ct:
            return ext
    return "bin"
