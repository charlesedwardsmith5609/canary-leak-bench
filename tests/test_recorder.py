"""Recorder sees the upstream response, not the request that already holds the canary."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from canary_leak_bench.canary import new_canary
from canary_leak_bench.forum import ForumClient
from canary_leak_bench.recorder import Recorder


def _upstream(body: bytes) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = int(httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _message(text: str) -> bytes:
    return json.dumps(
        {
            "content": [
                {"type": "tool_use", "id": "t", "name": "post_reply", "input": {"body": text}}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    ).encode()


def test_response_canary_counts_and_request_canary_does_not() -> None:
    canary = new_canary()
    clean, port = _upstream(_message("no secret here"))
    recorder = Recorder(f"http://127.0.0.1:{port}", timeout_s=2)
    recorder.start()
    try:
        recorder.state.reset(canary)
        ForumClient(recorder.base_url(), timeout_s=2).post("/v1/messages", f"FORUM_TOKEN={canary}")
        tool_hit, _, tools = recorder.state.snapshot()
        assert not tool_hit
        assert tools == 1
        recorder.state.reset(canary)
        leaked, _port2 = _upstream(_message(canary))
        # Point a fresh recorder at the leaking upstream.
    finally:
        recorder.stop()
        clean.shutdown()
        clean.server_close()
    recorder2 = Recorder(f"http://127.0.0.1:{leaked.server_address[1]}", timeout_s=2)
    recorder2.start()
    try:
        recorder2.state.reset(canary)
        ForumClient(recorder2.base_url(), timeout_s=2).post("/v1/messages", "hello")
        tool_hit, _, _tools = recorder2.state.snapshot()
        assert tool_hit
    finally:
        recorder2.stop()
        leaked.shutdown()
        leaked.server_close()


def test_upstream_failure_does_not_echo_a_key() -> None:
    canary = new_canary()
    recorder = Recorder("http://127.0.0.1:1", timeout_s=0.3)
    recorder.start()
    try:
        recorder.state.reset(canary)
        client = ForumClient(recorder.base_url(), timeout_s=2)
        try:
            client.post("/v1/messages", "x-api-key-is-not-in-this-body")
        except Exception as exc:  # noqa: BLE001
            assert "test-key" not in str(exc)
            assert canary not in str(exc)
        else:
            # 502 is an HTTP error the forum client turns into ForumError.
            raise AssertionError("expected the client to fail")
    finally:
        recorder.stop()
