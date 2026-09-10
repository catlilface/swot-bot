"""Downloader sub-settings.

Read from the environment via ``SECTION__FIELD`` vars with the ``__`` delimiter
(``env_nested_delimiter``), e.g. ``DOWNLOADER__MAX_VIDEO_DURATION_SEC=3600``;
unset fields fall back to the defaults below.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DownloaderSettings(BaseSettings):
    """Video download limits (DOWNLOADER__*).

    ``DOWNLOADER__ALLOWED_SOURCES``, ``DOWNLOADER__MAX_VIDEO_DURATION_SEC``,
    ``DOWNLOADER__RETENTION_HOURS``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DOWNLOADER__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    allowed_sources: str = (
        ""  # comma-separated host allowlist, e.g. "rutube.ru,disk.yandex.ru"
    )
    max_video_duration_sec: int = 7200  # skip longer videos
    retention_hours: int = 168  # keep unprocessed artifacts for N hours
