"""Provider error text on a real loopback server. The key must not come back out."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from canary_leak_bench.errors import ProviderError
from canary_leak_bench.provider import AnthropicProvider

_KEY = "test-key-not-real"


def _server(body: bytes, status: int = 200) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = body.replace(b"KEY", _KEY.encode())
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = int(httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def test_error_body_that_echoes_the_key_is_not_in_the_exception() -> None:
    httpd, port = _server(b"KEY", status=400)
    try:
        provider = AnthropicProvider(_KEY, f"http://127.0.0.1:{port}")
        try:
            provider.complete(
                model_id="m",
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
                timeout_s=2,
            )
        except ProviderError as exc:
            text = str(exc)
        else:
            raise AssertionError("expected ProviderError")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert _KEY not in text
    assert "HTTP 400" in text


def test_silent_server_hits_the_deadline() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    outcome: list[str] = []

    def call() -> None:
        provider = AnthropicProvider(_KEY, f"http://127.0.0.1:{port}")
        try:
            provider.complete(
                model_id="m",
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
                timeout_s=0.3,
            )
        except ProviderError as exc:
            outcome.append(str(exc))

    worker = threading.Thread(target=call)
    worker.start()
    worker.join(2)
    listener.close()
    assert not worker.is_alive()
    assert outcome == ["request timed out"]
    assert _KEY not in outcome[0]


def test_invalid_json_and_missing_usage_are_errors() -> None:
    for body in (b"not-json", b'{"content":[],"usage":{}}'):
        httpd, port = _server(body)
        try:
            with pytest.raises(ProviderError) as caught:
                AnthropicProvider(_KEY, f"http://127.0.0.1:{port}").complete(
                    model_id="m",
                    system="s",
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=16,
                    timeout_s=2,
                )
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert _KEY not in str(caught.value)


def test_success_parses_a_tool_call() -> None:
    payload = json.dumps(
        {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "fetch_page",
                    "input": {"path": "/threads/1"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 3, "output_tokens": 4},
        }
    ).encode()
    httpd, port = _server(payload)
    try:
        turn = AnthropicProvider(_KEY, f"http://127.0.0.1:{port}").complete(
            model_id="m",
            system="s",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=16,
            timeout_s=2,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert turn.tool_calls[0].name == "fetch_page"
    assert turn.input_tokens == 3
    assert _KEY not in turn.prose
