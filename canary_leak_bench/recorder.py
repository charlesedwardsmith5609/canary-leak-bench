"""Loopback upstream in front of the real API and behind agent-guard.

Handler threads set two booleans under a lock and discard the body. The runner
thread calls reset before a trial and snapshot after the HTTP call returns.
It never logs headers or bodies. The API key is forwarded and not stored.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from canary_leak_bench.errors import ForumError, ProviderError
from canary_leak_bench.http_post import MAX_BODY_BYTES, post_bytes
from canary_leak_bench.scan import scan_response

_FORWARDED = ("content-type", "x-api-key", "anthropic-version")
_TIMEOUT_S = 2.0


class ScanState:
    """Flags for the current canary. Handlers write; the runner reads after the call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gen = 0
        self._canary = ""
        self.tool_hit = False
        self.prose_hit = False
        self.tool_uses = 0

    def reset(self, canary: str) -> None:
        with self._lock:
            self._gen += 1
            self._canary = canary
            self.tool_hit = False
            self.prose_hit = False
            self.tool_uses = 0

    def observe(self, body: bytes) -> None:
        with self._lock:
            generation = self._gen
            canary = self._canary
        tool_hit, prose_hit, tools = scan_response(body, canary)
        with self._lock:
            if generation != self._gen:
                return
            self.tool_hit = self.tool_hit or tool_hit
            self.prose_hit = self.prose_hit or prose_hit
            self.tool_uses += tools

    def snapshot(self) -> tuple[bool, bool, int]:
        with self._lock:
            return self.tool_hit, self.prose_hit, self.tool_uses


def _handler(state: ScanState, upstream: str, timeout_s: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = _TIMEOUT_S

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._send(400, b"rejected\n")
                return
            try:
                body = self.rfile.read(length)
            except (TimeoutError, OSError):
                self.close_connection = True
                return
            forwarded = {
                name: self.headers[name]
                for name in _FORWARDED
                if self.headers.get(name) is not None
            }
            try:
                status, response = post_bytes(upstream, "/v1/messages", forwarded, body, timeout_s)
            except ProviderError:
                self._send(502, b"upstream failed\n")
                return
            state.observe(response)
            self._send(status, response)

        def _send(self, status: int, payload: bytes) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except (TimeoutError, OSError):
                self.close_connection = True
            self.close_connection = True

    return Handler


class Recorder:
    """The runner thread starts and stops this. Handlers only touch ScanState."""

    def __init__(self, upstream: str, timeout_s: float) -> None:
        self.state = ScanState()
        handler = _handler(self.state, upstream, timeout_s)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="recorder", daemon=True
        )
        self._started = False

    def start(self) -> None:
        self._thread.start()
        self._started = True
        host, port = self.address
        deadline = time.monotonic() + _TIMEOUT_S
        ready = False
        while time.monotonic() < deadline and not ready:
            try:
                probe = socket.create_connection((host, port), timeout=0.2)
            except OSError:
                ready = False
            else:
                probe.close()
                ready = True
        if not ready:
            raise ForumError("recorder did not accept connections")

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if not self._started:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=_TIMEOUT_S)
        if self._thread.is_alive():
            raise ForumError("recorder thread did not stop")
        self._started = False
