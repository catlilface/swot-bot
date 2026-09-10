"""T-2.7: round-trip serialize/deserialize для каждого MessageType.

Критерий: любое сообщение шины переживает ``deserialize(serialize(m))``
без потери полей и типа; неизвестный ``msg_type`` → ``ValueError``.
"""

import json
from uuid import UUID

import pytest
from swot_bus.serialization import deserialize, serialize
from swot_contracts import (
    AnalysisFailed,
    AnalysisReady,
    BaseMessage,
    DownloadFailed,
    DownloadRequest,
    JobFailed,
    JobProgress,
    MessageType,
    SourceRef,
    TranscriptFailed,
    TranscriptReady,
    VideoDownloaded,
)

TASK = UUID("00000000-0000-0000-0000-0000000000a1")
TRACE = "t-roundtrip"
SRC = SourceRef(url="https://example.com/v.mp4", kind="generic")


def _samples() -> dict[MessageType, BaseMessage]:
    """По одному инстансу каждого MessageType с типичным наполнением."""
    return {
        MessageType.DOWNLOAD_REQUEST: DownloadRequest(
            task_id=TASK, trace_id=TRACE, source=SRC
        ),
        MessageType.VIDEO_DOWNLOADED: VideoDownloaded(
            task_id=TASK,
            trace_id=TRACE,
            source=SRC,
            media_path="/data/media/v.mp4",
            title="Лекция",
            duration_sec=120,
        ),
        MessageType.DOWNLOAD_FAILED: DownloadFailed(
            task_id=TASK, trace_id=TRACE, source=SRC, error="net down"
        ),
        MessageType.TRANSCRIPT_READY: TranscriptReady(
            task_id=TASK,
            trace_id=TRACE,
            source=SRC,
            base_dir="/data/artifacts/task",
            srt_path="/data/artifacts/task/transcript.srt",
            segments_path="/data/artifacts/task/segments.json",
            language="ru",
            duration_sec=120,
            title="Лекция",
        ),
        MessageType.TRANSCRIPT_FAILED: TranscriptFailed(
            task_id=TASK, trace_id=TRACE, source=SRC, error="asr 500"
        ),
        MessageType.ANALYSIS_READY: AnalysisReady(
            task_id=TASK,
            trace_id=TRACE,
            source=SRC,
            base_dir="/data/artifacts/task",
            summary_path="/data/artifacts/task/summary.json",
            title="Лекция",
            srt_path="/data/artifacts/task/transcript.srt",
        ),
        MessageType.ANALYSIS_FAILED: AnalysisFailed(
            task_id=TASK, trace_id=TRACE, source=SRC, error="llm timeout"
        ),
        MessageType.JOB_FAILED: JobFailed(
            task_id=TASK, trace_id=TRACE, stage="download", error="boom"
        ),
        MessageType.JOB_PROGRESS: JobProgress(
            task_id=TASK, trace_id=TRACE, stage="transcribing"
        ),
    }


@pytest.mark.parametrize("msg_type", list(MessageType))
def test_roundtrip_every_message_type(msg_type: MessageType) -> None:
    msg = _samples()[msg_type]
    body = serialize(msg)
    assert isinstance(body, bytes)
    restored = deserialize(body)
    assert type(restored) is type(msg), (
        f"{msg_type}: {type(msg).__name__} -> {type(restored).__name__}"
    )
    assert restored.model_dump(mode="json") == msg.model_dump(mode="json")


def test_roundtrip_preserves_message_id() -> None:
    msg = _samples()[MessageType.JOB_PROGRESS]
    assert deserialize(serialize(msg)).message_id == msg.message_id


def test_unknown_msg_type_raises_value_error() -> None:
    """Неизвестный type не превращается в None/пустое сообщение — ошибка."""
    body = json.dumps({"msg_type": "no.such.type", "task_id": str(TASK)}).encode()
    with pytest.raises(ValueError, match="no.such.type"):
        deserialize(body)


def test_missing_msg_type_raises_value_error() -> None:
    body = json.dumps({"task_id": str(TASK), "stage": "download"}).encode()
    with pytest.raises(ValueError, match="msg_type"):
        deserialize(body)
