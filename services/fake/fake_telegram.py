"""Dev stub for the Telegram Bot API (stdlib only).

Emulates the small part of the Bot API the swot-bot bot uses:

* ``getMe`` — bot identity;
* ``getUpdates`` — long-poll loop; serves updates queued via ``POST /inject``;
* ``sendMessage`` / ``sendDocument`` — logged, return a valid ``Message``;
* ``GET /media/sample.wav`` — a small generated WAV so the download step of
  the pipeline works without any internet access (self-contained dev stack).

E2E without a real Telegram: start the stack, then

    curl -X POST http://localhost:8081/inject -H 'Content-Type: application/json' \
        -d '{"text": "http://fake-tg:8081/media/sample.wav"}

The fake update arrives as a message from the configured admin, the bot
publishes ``download.request`` and asks for the subject tag. The tag is the
next injected message (normalized, appended to the end of the result):

    curl -X POST http://localhost:8081/inject -H 'Content-Type: application/json' \
        -d '{"text": "http://fake-tg:8081/media/sample.wav"}'
    curl -X POST http://localhost:8081/inject -H 'Content-Type: application/json' \
        -d '{"text": "Высшая Математика"}'

The bot later sends the summary + SRT (with the ``🏷️`` tag at the end) back to
the "chat" — visible in this container's logs.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import struct
import threading
import time
import wave
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

ADMIN_ID = int(os.environ.get("FAKE_ADMIN_ID", "12345678"))

_state_lock = threading.Lock()
_updates: deque[dict] = deque()
_counters = {"update": 0, "message": 0}
_SAMPLE_WAV: bytes | None = None


def _sample_wav() -> bytes:
    """Generate (once) a small 3s 440 Hz sine WAV — enough for the pipeline."""
    global _SAMPLE_WAV
    if _SAMPLE_WAV is None:
        sample_rate = 16000
        duration = 3.0
        frames = bytearray()
        for i in range(int(sample_rate * duration)):
            value = int(12000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            frames += struct.pack("<h", value)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(bytes(frames))
        _SAMPLE_WAV = buf.getvalue()
    return _SAMPLE_WAV


def _next_update_id() -> int:
    with _state_lock:
        _counters["update"] += 1
        return _counters["update"]


def _next_msg_id() -> int:
    with _state_lock:
        _counters["message"] += 1
        return _counters["message"]


def _parse_multipart(body: bytes, content_type: str) -> dict[str, bytes]:
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


class FakeTelegramHandler(BaseHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._body: bytes = b""

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: N802
        print("[fake-tg]", fmt % args, flush=True)

    # -- helpers ------------------------------------------------------------

    def _read_body(self) -> bytes:
        """Read the request body: Content-Length or chunked transfer encoding."""
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            body = bytearray()
            while True:
                size_line = self.rfile.readline().strip()
                size = int(size_line.split(b";")[0], 16)
                if size == 0:
                    while True:  # consume optional trailers
                        line = self.rfile.readline()
                        if line in (b"\r\n", b"\n", b""):
                            break
                    break
                body += self.rfile.read(size)
                self.rfile.read(2)  # trailing CRLF
            self._body = bytes(body)
        else:
            length = int(self.headers.get("Content-Length") or 0)
            self._body = self.rfile.read(length)
        return self._body

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _form(self) -> dict[str, str]:
        """Request body as a flat dict: urlencoded or multipart (file parts kept separately)."""
        content_type = self.headers.get("Content-Type", "")
        body = self._read_body()
        if "multipart" in content_type:
            parts = _parse_multipart(body, content_type)
            return {
                k: v.decode(errors="replace")
                for k, v in parts.items()
                if k != "document"
            }
        return {k: v[0] for k, v in parse_qs(body.decode(errors="replace")).items()}

    def _file_part(self) -> bytes:
        parts = _parse_multipart(self._body, self.headers.get("Content-Type", ""))
        return parts.get("document", b"")

    def _message(
        self,
        chat_id: int,
        text: str | None = None,
        document: dict | None = None,
        thread_id: int | None = None,
    ) -> dict:
        message: dict = {
            "message_id": _next_msg_id(),
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "forum", "first_name": "Admin"},
        }
        if thread_id is not None:
            message["message_thread_id"] = thread_id
        if text is not None:
            message["text"] = text
        if document is not None:
            message["document"] = document
        return message

    # -- Bot API routes -------------------------------------------------------

    def _api(self, method: str) -> None:
        if method == "getMe":
            self._send_json(
                200,
                {
                    "ok": True,
                    "result": {
                        "id": 111,
                        "is_bot": True,
                        "first_name": "SwotFakeBot",
                        "username": "swot_fake_bot",
                        "can_join_groups": True,
                        "can_read_all_group_messages": False,
                        "supports_inline_queries": False,
                    },
                },
            )
            return

        if method == "getUpdates":
            query = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            timeout = min(float(query.get("timeout", ["10"])[0]), 25.0)
            deadline = time.monotonic() + timeout
            while True:
                with _state_lock:
                    update = _updates.popleft() if _updates else None
                if update is not None:
                    print(
                        f"[fake-tg] serving update: {json.dumps(update, ensure_ascii=False)}",
                        flush=True,
                    )
                    self._send_json(200, {"ok": True, "result": [update]})
                    return
                if time.monotonic() >= deadline:
                    self._send_json(200, {"ok": True, "result": []})
                    return
                time.sleep(0.25)

        if method in ("sendMessage", "sendDocument"):
            form = self._form()
            chat_id = int(form.get("chat_id", ADMIN_ID))
            thread_id = form.get("message_thread_id")
            thread_id = int(thread_id) if thread_id else None
            text = form.get("text")
            document = None
            if method == "sendDocument":
                file_bytes = self._file_part()
                document = {
                    "file_id": "BAFakeFileIdForDevStack",
                    "file_name": form.get("file_name", "transcript.srt"),
                    "file_unique_id": "BQFakeFileUniqueIdForDevStack",
                    "file_size": len(file_bytes),
                    "mime_type": "application/x-subrip",
                }
            print(
                f"[fake-tg] {method} chat_id={chat_id} "
                f"topic_id={thread_id} body={len(self._body)}b "
                f"form_keys={sorted(form)} text={text!r} "
                f"document={document and document.get('file_name')} "
                f"file_size={document and document.get('file_size')}",
                flush=True,
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "result": self._message(
                        chat_id, text, document, thread_id=thread_id
                    ),
                },
            )
            return

        self._send_json(
            404,
            {"ok": False, "error_code": 404, "description": f"unknown method {method}"},
        )

    # -- HTTP verbs ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/healthz"):
            self._send_json(200, {"ok": True})
            return
        if path == "/media/sample.wav":
            data = _sample_wav()
            self._send_bytes(200, "audio/wav", data)
            print(f"[fake-tg] served /media/sample.wav ({len(data)} bytes)", flush=True)
            return
        match = re.match(r"^/bot[^/]+/(\w+)$", path)
        if match is not None:
            self._api(match.group(1))
            return
        self._send_json(
            404, {"ok": False, "error_code": 404, "description": "not found"}
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/inject":
            body = json.loads(self._read_body() or b"{}")
            text = body.get("text", "")
            update = {
                "update_id": _next_update_id(),
                "message": {
                    "message_id": _next_msg_id(),
                    "date": int(time.time()),
                    "chat": {"id": ADMIN_ID, "type": "private", "first_name": "Admin"},
                    "from": {
                        "id": ADMIN_ID,
                        "is_bot": False,
                        "first_name": "Admin",
                        "username": "swot_admin",
                    },
                    "text": text,
                },
            }
            with _state_lock:
                _updates.append(update)
            print(f"[fake-tg] injected message: {text!r}", flush=True)
            self._send_json(200, {"queued": len(_updates)})
            return
        match = re.match(r"^/bot[^/]+/(\w+)$", path)
        if match is not None:
            self._api(match.group(1))
            return
        self._send_json(
            404, {"ok": False, "error_code": 404, "description": "not found"}
        )


def main() -> None:
    port = int(os.environ.get("FAKE_PORT", "8081"))
    server = ThreadingHTTPServer(("0.0.0.0", port), FakeTelegramHandler)
    print(f"[fake-tg] serving on :{port} (admin_id={ADMIN_ID})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
