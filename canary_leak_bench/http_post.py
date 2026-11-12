"""One bounded HTTP POST. The watchdog thread only shuts the socket down.

The calling thread owns the response bytes. The timer thread touches no other state.
"""

from __future__ import annotations

import http.client
import math
import socket
import threading
import time
from urllib.parse import urlsplit

from canary_leak_bench.errors import ProviderError

MAX_BODY_BYTES = 1 << 20
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def check_base_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return url.rstrip("/")
    if parts.scheme == "http" and host in LOOPBACK:
        return url.rstrip("/")
    raise ProviderError("base_url must be https, or http on loopback")


def post_bytes(
    base_url: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    timeout_s: float,
) -> tuple[int, bytes]:
    """POST and return (status, body). Does not raise on HTTP error statuses."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ProviderError("timeout must be a positive number")
    if len(body) > MAX_BODY_BYTES:
        raise ProviderError("request body too large")
    base = check_base_url(base_url)
    parts = urlsplit(base)
    host = parts.hostname or ""
    deadline = time.monotonic() + timeout_s
    conn_cls = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(host, parts.port, timeout=timeout_s)
    expired = threading.Event()

    def abort() -> None:
        expired.set()
        sock = conn.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watchdog = threading.Timer(timeout_s, abort)
    watchdog.daemon = True
    watchdog.start()
    try:
        conn.connect()
        if expired.is_set():
            raise ProviderError("request timed out")
        target = parts.path.rstrip("/") + path
        conn.request("POST", target, body=body, headers=headers)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderError("request timed out")
        if conn.sock is not None:
            conn.sock.settimeout(remaining)
        resp = conn.getresponse()
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("request timed out reading the response")
            if conn.sock is not None:
                conn.sock.settimeout(remaining)
            chunk = resp.read1(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise ProviderError("response body too large")
            chunks.append(chunk)
        if expired.is_set():
            raise ProviderError("request timed out")
        return resp.status, b"".join(chunks)
    except ProviderError:
        raise
    except (OSError, http.client.HTTPException, ValueError):
        if expired.is_set():
            raise ProviderError("request timed out") from None
        raise ProviderError("transport error") from None
    finally:
        watchdog.cancel()
        conn.close()
