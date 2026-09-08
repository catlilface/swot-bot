"""JSON serialization for RabbitMQ messages (pydantic v2)."""

from swot_contracts import BaseMessage, MessageType


def serialize(message: BaseMessage) -> bytes:
    """Serialize a typed message, embedding its type discriminator."""
    payload = message.model_dump(mode="json")
    payload["msg_type"] = message.msg_type.value
    return _json_dumps(payload).encode("utf-8")


def deserialize(body: bytes) -> BaseMessage:
    """Deserialize into the proper concrete message type from msg_type key."""
    data = _json_loads(body.decode("utf-8"))
    msg_type = data.get("msg_type")
    if msg_type is None:
        raise ValueError("missing msg_type in message")
    return _model_for(MessageType(msg_type)).model_validate(data)


def _model_for(msg_type: MessageType) -> type[BaseMessage]:
    from swot_contracts import (
        AnalysisFailed,
        AnalysisReady,
        DownloadFailed,
        DownloadRequest,
        JobFailed,
        JobProgress,
        TranscriptFailed,
        TranscriptReady,
        VideoDownloaded,
    )

    mapping: dict[MessageType, type[BaseMessage]] = {
        MessageType.DOWNLOAD_REQUEST: DownloadRequest,
        MessageType.VIDEO_DOWNLOADED: VideoDownloaded,
        MessageType.DOWNLOAD_FAILED: DownloadFailed,
        MessageType.TRANSCRIPT_READY: TranscriptReady,
        MessageType.TRANSCRIPT_FAILED: TranscriptFailed,
        MessageType.ANALYSIS_READY: AnalysisReady,
        MessageType.ANALYSIS_FAILED: AnalysisFailed,
        MessageType.JOB_FAILED: JobFailed,
        MessageType.JOB_PROGRESS: JobProgress,
    }
    try:
        return mapping[msg_type]
    except KeyError as exc:
        raise ValueError(f"unsupported msg_type: {msg_type}") from exc


def _json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _json_loads(text: str) -> dict:
    import json

    return json.loads(text)
