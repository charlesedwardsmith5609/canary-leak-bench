"""agent-guard is required for a live run of that config, and is not invented if missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from canary_leak_bench.cli import main
from canary_leak_bench.errors import ConfigError
from canary_leak_bench.guard import GuardProcess, require_binary

ROOT = Path(__file__).resolve().parents[1]


def test_missing_binary_raises() -> None:
    with pytest.raises(ConfigError):
        require_binary(Path("/no/such/agent-guard"))


def test_live_agent_guard_stops_before_reading_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = tmp_path / "bench.json"
    config.write_text(
        (ROOT / "examples" / "bench.example.json").read_text(encoding="utf-8")
        if (ROOT / "examples" / "bench.example.json").is_file()
        else (
            '{"model_id":"placeholder-model","api_key_env":"ANTHROPIC_API_KEY",'
            '"usd_per_mtok_in":"3.00","usd_per_mtok_out":"15.00"}\n'
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--cap-usd",
            "1.00",
            "--config",
            str(config),
            "--out",
            str(tmp_path / "report.md"),
            "--scenarios",
            str(ROOT / "scenarios" / "catalog.json"),
            "--only",
            "agent-guard",
            "--agent-guard-bin",
            str(tmp_path / "missing-binary"),
        ]
    )
    assert code == 2
    assert not (tmp_path / "report.md").exists()


def test_missing_binary_stops_the_whole_run_before_the_key_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(name: str) -> str:
        del name
        raise AssertionError("read the key")

    monkeypatch.setattr("canary_leak_bench.cli.key_from_env", boom)
    config = tmp_path / "bench.json"
    config.write_text(
        '{"model_id":"placeholder-model","api_key_env":"ANTHROPIC_API_KEY",'
        '"usd_per_mtok_in":"3.00","usd_per_mtok_out":"15.00"}\n',
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--cap-usd",
            "1.00",
            "--config",
            str(config),
            "--out",
            str(tmp_path / "report.md"),
            "--scenarios",
            str(ROOT / "scenarios" / "catalog.json"),
            "--agent-guard-bin",
            str(tmp_path / "missing-binary"),
        ]
    )
    assert code == 2


def test_fake_binary_starts_and_is_stopped(tmp_path: Path) -> None:
    argv_file = tmp_path / "argv.txt"
    script = tmp_path / "agent-guard"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > '{argv_file}'\n"
        "echo 'msg=listening addr=127.0.0.1:9 upstream=http://127.0.0.1:1' >&2\n"
        "exec sleep 30\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    guard = GuardProcess(script, "http://127.0.0.1:1", work)
    assert guard.start() == "http://127.0.0.1:9"
    policy = (work / "policy.yaml").read_text(encoding="utf-8")
    assert "fetch_page" in policy
    assert "hosts: []" in policy
    argv = argv_file.read_text(encoding="utf-8")
    assert "-audit-preview=false" in argv
    guard.stop()
