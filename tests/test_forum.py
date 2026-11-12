"""Forum on a real loopback listener. Every wait has its own deadline."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from canary_leak_bench.canary import new_canary
from canary_leak_bench.errors import ForumError
from canary_leak_bench.forum import Forum, ForumClient
from canary_leak_bench.scenarios import Page


def _page() -> tuple[Page, ...]:
    return (Page("/threads/1", "text/plain; charset=utf-8", "hello\n"),)


def test_post_records_only_the_request_that_carries_the_canary() -> None:
    canary = new_canary()
    forum = Forum(canary, _page())
    forum.start()
    try:
        client = ForumClient(forum.base_url(), timeout_s=2)
        assert "hello" in client.get("/threads/1")
        assert client.post("/submit", "nothing to see") == "ok\n"
        assert client.post("/submit", f"token {canary}") == "ok\n"
        hits = forum.hits()
    finally:
        forum.stop()
    seen = [hit for hit in hits if hit.canary_seen]
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert all(canary not in hit.path for hit in hits)


def test_two_clients_are_both_recorded() -> None:
    canary = new_canary()
    forum = Forum(canary, _page())
    forum.start()
    errors: list[BaseException] = []

    def post(body: str) -> None:
        try:
            ForumClient(forum.base_url(), timeout_s=2).post("/submit", body)
        except BaseException as exc:  # noqa: BLE001  re-raised below
            errors.append(exc)

    try:
        first = threading.Thread(target=post, args=("one",))
        second = threading.Thread(target=post, args=(canary,))
        first.start()
        second.start()
        first.join(2)
        second.join(2)
        assert not first.is_alive()
        assert not second.is_alive()
        assert errors == []
        posts = [hit for hit in forum.hits() if hit.method == "POST"]
        assert sum(1 for hit in posts if hit.canary_seen) == 1
        assert len(posts) == 2
    finally:
        forum.stop()


def test_client_read_times_out_instead_of_hanging() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    outcome: list[str] = []

    def call() -> None:
        try:
            ForumClient(f"http://127.0.0.1:{port}", timeout_s=0.3).get("/x")
        except ForumError:
            outcome.append("timed-out")

    worker = threading.Thread(target=call)
    worker.start()
    worker.join(2)
    listener.close()
    assert not worker.is_alive()
    assert outcome == ["timed-out"]


def test_trickle_is_closed_by_the_server() -> None:
    canary = new_canary()
    forum = Forum(canary, _page())
    forum.start()
    try:
        host, port = forum.address
        sock = socket.create_connection((host, port), timeout=2)
        sock.sendall(b"G")
        sock.settimeout(3)
        try:
            data = sock.recv(64)
        except TimeoutError:
            pytest.fail("server did not close a trickling client")
        finally:
            sock.close()
    finally:
        forum.stop()
    assert data == b""


def test_canary_in_the_path_or_a_header_is_a_hit_and_is_not_stored() -> None:
    canary = new_canary()
    forum = Forum(canary, _page())
    forum.start()
    try:
        host, port = forum.address
        ForumClient(forum.base_url(), timeout_s=2).post(f"/{canary}", "plain")
        sock = socket.create_connection((host, port), timeout=2)
        sock.sendall(
            (
                "POST /submit HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"X-Note: {canary}\r\n"
                "Content-Length: 2\r\n"
                "Connection: close\r\n\r\n"
                "hi"
            ).encode()
        )
        sock.settimeout(2)
        sock.recv(256)
        sock.close()
        hits = forum.hits()
    finally:
        forum.stop()
    assert sum(1 for hit in hits if hit.canary_seen) >= 2
    assert all(canary not in hit.path for hit in hits)


def test_ninth_connection_is_refused_while_eight_are_held() -> None:
    forum = Forum(new_canary(), _page())
    forum.start()
    held: list[socket.socket] = []
    try:
        host, port = forum.address
        for _ in range(8):
            sock = socket.create_connection((host, port), timeout=2)
            sock.sendall(b"G")
            held.append(sock)
        outcome: list[str] = []

        def ninth() -> None:
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and not outcome:
                try:
                    ForumClient(forum.base_url(), timeout_s=0.5).get("/threads/1")
                except ForumError:
                    outcome.append("refused")

        worker = threading.Thread(target=ninth)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert outcome == ["refused"]
    finally:
        for sock in held:
            sock.close()
        forum.stop()


def test_paced_bytes_cannot_extend_the_request() -> None:
    forum = Forum(new_canary(), _page())
    forum.start()
    try:
        host, port = forum.address
        sock = socket.create_connection((host, port), timeout=2)
        sock.sendall(b"G")
        sock.settimeout(4)

        def pace() -> None:
            for _ in range(8):
                try:
                    sock.sendall(b"G")
                except OSError:
                    return
                time.sleep(0.25)  # the attack: refresh an inactivity timer

        sender = threading.Thread(target=pace)
        sender.start()
        try:
            data = sock.recv(64)
        except TimeoutError:
            pytest.fail("absolute deadline did not close a trickling client")
        finally:
            sock.close()
            sender.join(2)
    finally:
        forum.stop()
    assert data == b""
    assert not sender.is_alive()


def test_stop_joins_the_accept_thread() -> None:
    forum = Forum(new_canary(), _page())
    forum.start()
    forum.stop()
    assert not forum._thread.is_alive()  # noqa: SLF001


def test_stderr_does_not_receive_the_canary(capsys: pytest.CaptureFixture[str]) -> None:
    canary = new_canary()
    forum = Forum(canary, _page())
    forum.start()
    try:
        ForumClient(forum.base_url(), timeout_s=2).post(f"/{canary}", canary)
    finally:
        forum.stop()
    captured = capsys.readouterr()
    assert canary not in captured.out
    assert canary not in captured.err
    assert all(canary not in hit.path for hit in forum.hits())
