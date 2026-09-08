"""Smoke tests: contracts and observability import and construct correctly."""

from swot_contracts import DownloadRequest, MessageType, SourceRef, TranscriptReady
from swot_observability import new_trace_id


def test_download_request_smoke() -> None:
    d = DownloadRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        trace_id=new_trace_id(),
        source=SourceRef(url="https://disk.yandex.ru/i/abc123", kind="yandex_disk"),
    )
    assert d.msg_type == MessageType.DOWNLOAD_REQUEST
    assert d.source.kind == "yandex_disk"
    assert d.source.url.startswith("https://")


def test_match_by_msg_type() -> None:
    tr = TranscriptReady(
        task_id="00000000-0000-0000-0000-000000000002",
        trace_id="t-x",
        source=SourceRef(url="https://example.com/video"),
        base_dir="/data/artifacts/2",
        srt_path="/data/artifacts/2/transcript.srt",
        segments_path="/data/artifacts/2/segments.json",
    )
    assert tr.msg_type == MessageType.TRANSCRIPT_READY
    assert tr.srt_path.endswith(".srt")
