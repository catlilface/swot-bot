"""Downloader port + domain model.

Only a protocol here; concrete per-source adapters live in ``adapters.py``.
Kept decoupled so the downloader can route to any source (yandex disk, google
drive, vk, rutube, direct file, generic page) — see docs/architecture.md §7.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from swot_contracts import SourceRef


class DownloadError(Exception):
    """Скачивание источника невозможно (таймаут, не-медиа content-type,
    превышение размера/длительности и т.п.). Сервис преобразует его в
    `JobFailed(stage="download")`.
    """


@dataclass(frozen=True)
class DownloadedMedia:
    """Result of a successful download."""

    media_path: Path
    title: str
    duration_sec: int
    resource_id: str | None = None


class MediaDownloader(Protocol):
    """Download a source into dst_dir and return its media location."""

    async def download(self, source: SourceRef, dst_dir: Path) -> DownloadedMedia: ...
