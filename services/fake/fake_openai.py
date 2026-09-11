"""Dev stubs for OpenAI-compatible endpoints (stdlib only).

Two modes (one image, different command):

* ``asr``: POST /v1/audio/transcriptions -> ``verbose_json`` transcription
  (fixed text with per-segment timings).
* ``llm``: POST /v1/chat/completions -> chat completion whose message is the
  fixed Summary JSON (handles both ``response_format=json_schema`` content and
  ``tools``/function-calling request shapes).

Both modes also serve ``GET /healthz`` for the compose healthcheck.
"""

from __future__ import annotations

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_TEXT = (
    "Привет, это фиксированная расшифровка для прогона dev-стека swot-bot. "
    "Первый сегмент описывает вступление лекции. Второй сегмент — основную тему. "
    "Третий сегмент — выводы, практические задания и рекомендации слушателям курса."
)

FAKE_SEGMENTS = [
    {
        "id": 0,
        "start": 0.0,
        "end": 5.2,
        "text": "Привет, это фиксированная расшифровка для прогона dev-стека swot-bot.",
    },
    {
        "id": 1,
        "start": 5.2,
        "end": 12.4,
        "text": "Первый сегмент описывает вступление лекции.",
    },
    {"id": 2, "start": 12.4, "end": 19.8, "text": "Второй сегмент — основную тему."},
    {
        "id": 3,
        "start": 19.8,
        "end": 27.0,
        "text": "Третий сегмент — выводы, практические задания и рекомендации слушателям курса.",
    },
]

FAKE_SUMMARY = {
    # "hashtags": в dev-стеке демонстрирует контент-хэштеги (#задания)
    # в доставляемом сообщении; бот отдаёт только известные значения.
    "hashtags": ["задания"],
    "summary": "Фиксированное саммари: dev-стек swot-bot работает (asr/llm — фэйки).",
    "sections": [
        {
            "heading": "Вступление",
            "facts": [
                {"text": "Сегмент 1: вступление лекции.", "start_sec": 5.2},
                {"text": "Сегмент 2: основная тема.", "start_sec": 12.4},
            ],
        },
        {
            "heading": "Выводы",
            "facts": [
                {"text": "Сегмент 3: практические рекомендации.", "start_sec": 19.8}
            ],
        },
    ],
}


def _parse_multipart(body: bytes, content_type: str) -> dict[str, bytes]:
    """Minimal multipart/form-data parser (no `cgi` module in Python 3.13)."""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if match is None:
        return {}
    boundary = match.group(1).encode()
    out: dict[str, bytes] = {}
    for part in body.split(b"--" + boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue
        name_match = re.search(r'name="([^"]+)"', header_blob.decode("latin-1"))
        if name_match is None:
            continue
        out[name_match.group(1)] = content.rstrip(b"\r\n")
    return out


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class FakeAsrHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible audio transcription stub."""

    def log_message(self, fmt: str, *args: object) -> None:
        print("[fake-asr]", fmt % args, flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("", "/healthz"):
            _json(self, 200, {"ok": True})
        else:
            _json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path.rstrip("/").endswith(
            "/v1/audio/transcriptions"
        ) or self.path.rstrip("/").endswith("/audio/transcriptions"):
            parts = _parse_multipart(body, self.headers.get("Content-Type", ""))
            if "file" not in parts:
                _json(
                    self,
                    400,
                    {
                        "error": {
                            "message": "missing 'file' part",
                            "type": "invalid_request",
                        }
                    },
                )
                return
            print(
                f"[fake-asr] transcription: {len(parts['file'])} bytes, "
                f"model={parts.get('model', b'').decode(errors='replace')!r}",
                flush=True,
            )
            _json(
                self,
                200,
                {
                    "task": "transcribe",
                    "language": "ru",
                    "duration": 27.0,
                    "text": FAKE_TEXT,
                    "segments": FAKE_SEGMENTS,
                },
            )
            return
        _json(self, 404, {"error": {"message": "not found", "type": "invalid_request"}})


def _llm_response(body: dict) -> dict:
    """Build a chat completion for a fake model.

    Content-based JSON when ``response_format`` asks for it, tool-call based
    when the client sent ``tools`` (langchain_openai structured outputs use
    either shape depending on version/model).
    """
    content = json.dumps(FAKE_SUMMARY, ensure_ascii=False)
    base = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "fake-model"),
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    response_format = body.get("response_format") or {}
    uses_tools = bool(body.get("tools"))
    if uses_tools:
        function = {
            "name": "summary",
            "arguments": content,
        }
        message: dict = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-fake", "type": "function", "function": function}
            ],
        }
        finish_reason = "tool_calls"
    elif response_format.get("type") == "json_schema":
        message = {"role": "assistant", "content": content}
        finish_reason = "stop"
    else:
        message = {"role": "assistant", "content": content}
        finish_reason = "stop"
    return {
        **base,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


class FakeLlmHandler(BaseHTTPRequestHandler):
    """OpenAI-compatible chat completions stub."""

    def log_message(self, fmt: str, *args: object) -> None:
        print("[fake-llm]", fmt % args, flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("", "/healthz"):
            _json(self, 200, {"ok": True})
        else:
            _json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.rstrip("/").endswith("/v1/chat/completions"):
            print(
                f"[fake-llm] chat completion request, model={body.get('model')!r}",
                flush=True,
            )
            _json(self, 200, _llm_response(body))
            return
        _json(self, 404, {"error": {"message": "not found", "type": "invalid_request"}})


def main() -> None:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "asr").lower()
    port = int(os.environ.get("FAKE_PORT", "8000"))
    handler = FakeAsrHandler if mode == "asr" else FakeLlmHandler
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"[fake] serving {mode!r} on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
