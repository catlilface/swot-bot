"""Per-source download adapters using yt-dlp and direct HTTP.

Routing: SourceRouter picks the right adapter by URL type; the DTLA generic
extractor via yt-dlp covers most pages, while a dedicated passthrough adapter
handles direct media URLs (no site extraction needed).
"""

from pathlib import Path
from urllib.parse import urlparse

from swot_contracts import SourceRef

from .domain import DownloadedMedia


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
    """Choose an adapter based on the request kind / URL."""

    def __init__(self, yt_dlp: YtDlpAdapter, direct: DirectHttpAdapter) -> None:
        self._yt_dlp = yt_dlp
        self._direct = direct
        self._direct_hosts = {
            "youtube.com",
            "www.youtube.com",
            "youtu.be",
            "yandex.ru",
            "disk.yandex.ru",
            "drive.google.com",
            "vk.com",
            "vkvideo.ru",
            "rutube.ru",
        }

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia:
        if self._is_direct_url(source.url):
            return await self._direct.download(source, dst_dir)
        return await self._yt_dlp.download(source, dst_dir)

    def _is_direct_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return bool(host) and host in self._direct_hosts


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
