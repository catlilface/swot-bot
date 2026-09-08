"""Event/message models for the swot-bot pipeline.

Conventions (see docs/architecture.md):
- lightweight data (source URL, task_id, paths, metadata) travels in RabbitMQ
  events; heavy artifacts (media, transcripts, summaries) live on the shared
  volume and only *paths* are passed in events.
- ``start_sec`` on a fact is the time in the video where the fact was said
  (rendered as MM:SS, not necessarily a URL).
"""

import enum
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessageType(enum.StrEnum):
    DOWNLOAD_REQUEST = "download.request"
    VIDEO_DOWNLOADED = "video.downloaded"
    DOWNLOAD_FAILED = "download.failed"
    TRANSCRIBE_REQUEST = "transcribe.request"
    TRANSCRIPT_READY = "transcript.ready"
    TRANSCRIPT_FAILED = "transcript.failed"
    ANALYSIS_REQUEST = "analysis.request"
    ANALYSIS_READY = "analysis.ready"
    ANALYSIS_FAILED = "analysis.failed"
    JOB_FAILED = "job.failed"
    JOB_PROGRESS = "job.progress"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SourceRef(BaseModel):
    """Reference to the source video/lecture."""

    url: str
    kind: str = "generic"  # yandex_disk | gdrive | vkvideo | rutube | direct | generic
    resource_id: str | None = None


class BaseMessage(BaseModel):
    message_id: UUID = Field(default_factory=uuid4)
    msg_type: MessageType
    task_id: UUID
    trace_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)


class DownloadRequest(BaseMessage):
    """bot -> downloader: admin submitted a link."""

    msg_type: MessageType = MessageType.DOWNLOAD_REQUEST
    source: SourceRef


class VideoDownloaded(BaseMessage):
    """downloader -> transcriber: media saved to shared volume."""

    msg_type: MessageType = MessageType.VIDEO_DOWNLOADED
    source: SourceRef
    media_path: str
    title: str
    duration_sec: int
    resource_id: str | None = None


class TranscribeRequest(BaseMessage):
    """downloader -> transcriber (usually merged into VideoDownloaded)."""

    msg_type: MessageType = MessageType.TRANSCRIBE_REQUEST
    source: SourceRef
    media_path: str
    duration_sec: int = 0


class TranscriptReady(BaseMessage):
    """transcriber -> analyzer: SRT + segments written to volume."""

    msg_type: MessageType = MessageType.TRANSCRIPT_READY
    source: SourceRef
    base_dir: str
    srt_path: str
    segments_path: str
    language: str = ""
    duration_sec: int = 0


class AnalysisRequest(BaseMessage):
    """transcriber -> analyzer (usually merged into TranscriptReady)."""

    msg_type: MessageType = MessageType.ANALYSIS_REQUEST
    source: SourceRef
    base_dir: str
    transcript_path: str


class AnalysisReady(BaseMessage):
    """analyzer -> bot: structured summary is ready for publishing."""

    msg_type: MessageType = MessageType.ANALYSIS_READY
    source: SourceRef
    base_dir: str
    summary_path: str
    title: str = ""


class JobFailed(BaseMessage):
    """fanout: any stage failed -> bot notifies admin; body -> DLQ."""

    msg_type: MessageType = MessageType.JOB_FAILED
    stage: str
    error: str


class JobProgress(BaseMessage):
    """fanout: optional progress updates ("downloading/transcribing/analyzing")."""

    msg_type: MessageType = MessageType.JOB_PROGRESS
    stage: str


class DownloadFailed(BaseMessage):
    msg_type: MessageType = MessageType.DOWNLOAD_FAILED
    source: SourceRef
    error: str


class TranscriptFailed(BaseMessage):
    msg_type: MessageType = MessageType.TRANSCRIPT_FAILED
    source: SourceRef
    error: str


class AnalysisFailed(BaseMessage):
    msg_type: MessageType = MessageType.ANALYSIS_FAILED
    source: SourceRef
    error: str
