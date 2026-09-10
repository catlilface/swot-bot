"""Per-source download adapters using yt-dlp and direct HTTP.

Routing: ``SourceRouter.route()`` returns the adapter to use for a URL:
platform hosts (yt-dlp knows their extractors) → ``YtDlpAdapter``; direct
media URLs (media extension, or ``Content-Type: video/*|audio/*`` per HEAD)
→ ``DirectHttpAdapter``; anything else → ``YtDlpAdapter`` (default).
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from swot_contracts import SourceRef

from .domain import DownloadedMedia

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
    """Generic media downloader via yt-dlp (Python API no CLI)."""

    def __init__(self, timeout_sec: int = 60) -> None:
        self._timeout = timeout_sec

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        import yt_dlp

        dst_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(dst_dir / "media.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source.url, download=True)
        path = _resolve_media(dst_dir)
        return DownloadedMedia(
            media_path=path,
            title=info.get("title") or source.url,
            duration_sec=int(info.get("duration") or 0),
            resource_id=str(info.get("id")) if info.get("id") else None,
        )


class DirectHttpAdapter:
    """Download a direct media URL (mp4/webm/m4a etc.) via plain HTTP."""

    AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".aac", ".flac"}
    VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".avi"}

    def __init__(self, timeout_sec: int = 120) -> None:
        self._timeout = timeout_sec

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        import aiohttp

        dst_dir.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(self._timeout)
        ) as session:
            async with session.get(source.url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                ext = _extension_from_content_type(content_type)
                target = dst_dir / f"media.{ext}"
                with target.open("wb") as fh:
                    async for chunk in resp.content.iter_chunked(2**16):
                        fh.write(chunk)
        return DownloadedMedia(
            media_path=target, title=source.url, duration_sec=0, resource_id=None
        )


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
