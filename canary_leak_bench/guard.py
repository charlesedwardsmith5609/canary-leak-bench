"""Start and stop an agent-guard process. The runner thread owns the handle.

A second thread reads stderr so the pipe cannot fill. It records only the
listen address. Stderr text is not kept, because it is not a place for secrets
and it is not useful in errors.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from pathlib import Path

from canary_leak_bench.errors import ConfigError

_ADDR = re.compile(r"addr=(127\.0\.0\.1:\d+)")
_READY_S = 5.0
_STOP_S = 5.0


def resolve_binary(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    env = os.environ.get("AGENT_GUARD_BIN", "")
    if not env:
        return None
    path = Path(env)
    return path if path.is_file() else None


def require_binary(explicit: Path | None) -> Path:
    found = resolve_binary(explicit)
    if found is None:
        raise ConfigError(
            "a live agent-guard config needs the agent-guard binary "
            "(--agent-guard-bin or AGENT_GUARD_BIN). Refusing to spend without it."
        )
    return found


def write_policy(directory: Path) -> Path:
    work = directory.resolve()
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", str(work)):
        raise ConfigError("refusing to write a policy into a path with unusual characters")
    text = (
        "tools:\n"
        "  - fetch_page\n"
        "  - post_reply\n"
        "hosts: []\n"
        "paths:\n"
        f"  workdir: {work}\n"
        "  allow:\n"
        f"    - {work}\n"
        "sensitive:\n"
        "  proc_environ: block\n"
        "  ssh: block\n"
        "  dotenv: block\n"
        "  metadata: block\n"
    )
    path = work / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class GuardProcess:
    def __init__(self, binary: Path, upstream: str, directory: Path) -> None:
        self._binary = binary
        self._upstream = upstream
        self._directory = directory
        self._proc: subprocess.Popen[str] | None = None
        self._addr = ""
        self._ready = threading.Event()
        self._reader: threading.Thread | None = None

    def start(self) -> str:
        policy = write_policy(self._directory)
        audit = self._directory / "audit.jsonl"
        argv = [
            str(self._binary),
            "-config",
            str(policy),
            "-audit-log",
            str(audit),
            "-audit-preview=false",
            "-listen",
            "127.0.0.1:0",
            "-upstream",
            self._upstream,
            "-otel",
            "none",
        ]
        self._proc = subprocess.Popen(  # noqa: S603  binary and args are a list, not a shell
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._reader = threading.Thread(target=self._read_stderr, name="guard-stderr", daemon=True)
        self._reader.start()
        if not self._ready.wait(timeout=_READY_S):
            self.stop()
            raise ConfigError("agent-guard did not become ready")
        return f"http://{self._addr}"

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            match = _ADDR.search(line)
            if match and not self._ready.is_set():
                self._addr = match.group(1)
                self._ready.set()

    def stop(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=_STOP_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=_STOP_S)
        if self._reader is not None:
            self._reader.join(timeout=_STOP_S)
        self._proc = None
