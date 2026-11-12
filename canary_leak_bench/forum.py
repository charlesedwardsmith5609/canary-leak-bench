"""Fake forum. Handler threads append hits under HitLog's lock; the runner reads later.

Concurrency: ThreadingHTTPServer. One lock guards the hit list. It is taken only
after the request bytes are in memory, never around a socket read or write.
The accept thread is the server thread. Handlers are daemons. No asyncio.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from canary_leak_bench.canary import contains_bytes, is_canary
from canary_leak_bench.errors import ForumError
from canary_leak_bench.scenarios import Page

_MAX_BODY = 64 * 1024
_TIMEOUT_S = 2.0
_READY_DEADLINE_S = 2.0


@dataclass(frozen=True)
class Hit:
    method: str
    path: str
    canary_seen: bool


class HitLog:
    """The hit list. Handler threads call add; the runner thread calls snapshot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: list[Hit] = []

    def add(self, hit: Hit) -> None:
        with self._lock:
            self._hits.append(hit)

    def snapshot(self) -> tuple[Hit, ...]:
        with self._lock:
            return tuple(self._hits)


def _record(log: HitLog, method: str, path: str, token: str, seen: bool) -> None:
    log.add(Hit(method=method, path=path[:200].replace(token, "[CANARY]"), canary_seen=seen))


class _Gate:
    """Sockets and handler threads. stop() aborts sockets, then joins threads.

    The timer threads that shut a socket down touch no other state. The runner
    thread is the only caller of abort and join.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._socks: set[socket.socket] = set()
        self._threads: set[threading.Thread] = set()

    def add_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._threads.add(thread)

    def discard_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._threads.discard(thread)

    def add_sock(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.add(sock)

    def discard_sock(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.discard(sock)

    def abort(self) -> None:
        with self._lock:
            socks = list(self._socks)
        for sock in socks:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def join(self, timeout_s: float) -> bool:
        with self._lock:
            threads = list(self._threads)
        deadline = time.monotonic() + timeout_s
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        return all(not thread.is_alive() for thread in threads)


def _abort_socket(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class _BoundServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        gate: _Gate,
        slots: threading.BoundedSemaphore,
    ) -> None:
        self._gate = gate
        self._slots = slots
        super().__init__(server_address, handler)
        self.daemon_threads = True

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:  # type: ignore[override]
        if not self._slots.acquire(blocking=False):
            _abort_socket(request)
            return

        def run() -> None:
            try:
                self.process_request_thread(request, client_address)
            finally:
                self._slots.release()
                self._gate.discard_thread(threading.current_thread())

        thread = threading.Thread(target=run, daemon=True)
        self._gate.add_thread(thread)
        thread.start()


def _handler(
    log: HitLog,
    pages: Mapping[str, bytes],
    types: Mapping[str, str],
    canary: bytes,
    gate: _Gate,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = _TIMEOUT_S

        def log_message(self, fmt: str, *args: object) -> None:
            return

        def handle(self) -> None:
            # The slot was acquired before this thread was started.
            gate.add_sock(self.request)
            timer = threading.Timer(_TIMEOUT_S, _abort_socket, args=(self.request,))
            timer.daemon = True
            timer.start()
            try:
                super().handle()
            finally:
                timer.cancel()
                gate.discard_sock(self.request)

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except TimeoutError:
                self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802
            self._serve("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._serve("POST")

        def _serve(self, method: str) -> None:
            target = self.path if isinstance(self.path, str) else "/"
            parts = urlsplit(target)
            path = parts.path or "/"
            header_blob = b"".join(
                name.encode("latin-1", errors="ignore")
                + b":"
                + value.encode("latin-1", errors="ignore")
                for name, value in self.headers.items()
            )
            token = canary.decode("ascii")
            seen = contains_bytes(
                target.encode("latin-1", errors="ignore"), token
            ) or contains_bytes(header_blob, token)
            body = b""
            if method == "POST":
                read = self._read_body()
                if read is None:
                    _record(log, method, path, token, seen)
                    return
                body = read
                seen = seen or contains_bytes(body, token)
            _record(log, method, path, token, seen)
            if method == "GET" and path in pages:
                self._send(200, types.get(path, "text/plain; charset=utf-8"), pages[path])
                return
            if method == "POST":
                self._send(200, "text/plain; charset=utf-8", b"ok\n")
                return
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def _read_body(self) -> bytes | None:
            raw = self.headers.get("Content-Length")
            if raw is None:
                self._send(411, "text/plain; charset=utf-8", b"length required\n")
                return None
            try:
                length = int(raw)
            except ValueError:
                self._send(400, "text/plain; charset=utf-8", b"bad length\n")
                return None
            if length < 0 or length > _MAX_BODY:
                self._send(413, "text/plain; charset=utf-8", b"body too large\n")
                return None
            try:
                return self.rfile.read(length)
            except TimeoutError:
                self.close_connection = True
                return None

        def _send(self, status: int, content_type: str, payload: bytes) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except (TimeoutError, OSError):
                self.close_connection = True
            self.close_connection = True

    return Handler


class Forum:
    """One scenario's forum. start/stop are called on the runner thread."""

    def __init__(self, canary: str, pages: tuple[Page, ...]) -> None:
        if not is_canary(canary):
            raise ForumError("forum requires a generated canary")
        self._canary = canary
        self._log = HitLog()
        self._gate = _Gate()
        self._slots = threading.BoundedSemaphore(8)
        bodies = {page.path: page.body.encode("utf-8") for page in pages}
        types = {page.path: page.content_type for page in pages}
        handler = _handler(self._log, bodies, types, canary.encode("ascii"), self._gate)
        self._httpd = _BoundServer(("127.0.0.1", 0), handler, self._gate, self._slots)
        self._httpd.timeout = _TIMEOUT_S
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="forum", daemon=True)
        self._started = False

    def start(self) -> None:
        self._thread.start()
        self._started = True
        self._wait_until_listening()

    def _wait_until_listening(self) -> None:
        host, port = self.address
        deadline = time.monotonic() + _READY_DEADLINE_S
        ready = False
        while time.monotonic() < deadline and not ready:
            try:
                probe = socket.create_connection((host, port), timeout=0.2)
                probe.sendall(
                    b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
                )
                probe.settimeout(0.5)
                ready = bool(probe.recv(64))
            except OSError:
                ready = False
            else:
                probe.close()
        if not ready:
            raise ForumError("forum did not accept connections")

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._httpd.server_address[:2]
        return str(host), int(port)

    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    def hits(self) -> tuple[Hit, ...]:
        return self._log.snapshot()

    def stop(self) -> None:
        if not self._started:
            return
        self._httpd.shutdown()
        self._gate.abort()
        handlers_done = self._gate.join(_TIMEOUT_S + 1)
        self._httpd.server_close()
        self._thread.join(timeout=_TIMEOUT_S)
        self._started = False
        if self._thread.is_alive() or not handlers_done:
            raise ForumError("forum thread did not stop")


class ForumClient:
    """Blocking client. Every call uses the given timeout. The runner thread owns it."""

    def __init__(self, base_url: str, timeout_s: float = _TIMEOUT_S) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost"}:
            raise ForumError("forum client only talks to a loopback forum")
        if parts.port is None:
            raise ForumError("forum client requires a port")
        self._host = parts.hostname
        self._port = parts.port
        self._timeout = timeout_s

    def get(self, path: str) -> str:
        return self._request("GET", path, None)

    def post(self, path: str, body: str) -> str:
        return self._request("POST", path, body.encode("utf-8"))

    def _request(self, method: str, path: str, body: bytes | None) -> str:
        if not path.startswith("/") or "\r" in path or "\n" in path:
            raise ForumError("refusing a path that is not a single absolute path")
        try:
            sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except (TimeoutError, OSError) as exc:
            raise ForumError(f"forum transport error: {type(exc).__name__}") from None
        try:
            sock.settimeout(self._timeout)
            payload = body if body is not None else b""
            header = (
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: {self._host}:{self._port}\r\n"
                "Connection: close\r\n"
            )
            if body is not None:
                header += f"Content-Length: {len(payload)}\r\n"
            sock.sendall((header + "\r\n").encode("ascii") + payload)
            chunks: list[bytes] = []
            while True:
                try:
                    piece = sock.recv(8192)
                except TimeoutError as exc:
                    raise ForumError("forum read timed out") from exc
                if not piece:
                    break
                chunks.append(piece)
                if sum(len(c) for c in chunks) > _MAX_BODY + 4096:
                    raise ForumError("forum response too large")
        except TimeoutError as exc:
            raise ForumError("forum connect or send timed out") from exc
        except OSError as exc:
            raise ForumError(f"forum transport error: {type(exc).__name__}") from None
        finally:
            sock.close()
        raw = b"".join(chunks)
        head, _, rest = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0]
        parts = status_line.split(b" ")
        if len(parts) < 2 or not parts[1].isdigit():
            raise ForumError("forum returned a malformed status")
        code = int(parts[1])
        if code >= 400:
            raise ForumError(f"forum HTTP {code}")
        return rest.decode("utf-8", errors="replace")
