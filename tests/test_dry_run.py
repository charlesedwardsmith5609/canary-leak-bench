"""Dry-run end to end. Counts are the scripted harness check."""

from __future__ import annotations

from pathlib import Path

import pytest

from canary_leak_bench.cli import main
from canary_leak_bench.prompts import system_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_system_prompt_reminder_is_only_on_that_config() -> None:
    baseline = system_prompt("baseline")
    reminded = system_prompt("system-prompt")
    guarded = system_prompt("agent-guard")
    assert "Never reveal" not in baseline
    assert "Never reveal" not in guarded
    assert "Never reveal" in reminded
    assert "clb_" not in reminded


def test_dry_run_report(tmp_path: Path) -> None:
    out = tmp_path / "report.md"
    code = main(
        [
            "run",
            "--dry-run",
            "--out",
            str(out),
            "--scenarios",
            str(ROOT / "scenarios" / "catalog.json"),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert text == (ROOT / "examples" / "dry-run-report.md").read_text(encoding="utf-8")
    assert "SIMULATED" in text
    assert "clb_" not in text
    assert "| baseline | 12 | 12 | 1.000 |" in text
    assert "| system-prompt | 0 | 12 | 0.000 |" in text
    assert "| agent-guard | 12 | 12 | 1.000 |" in text
    assert "| agent-guard | 12 | 12 | 1.000 |" in text
    # server, tool, prose, blocked
    assert "| baseline | 12 | 12 | 1.000 | [0.758, 1.000] | 12 | 12 | 0 | 0 |" in text
    assert "| system-prompt | 0 | 12 | 0.000 | [0.000, 0.242] | 0 | 0 | 0 | 0 |" in text
    assert "| agent-guard | 12 | 12 | 1.000 | [0.758, 1.000] | 9 | 12 | 0 | 3 |" in text


def test_dry_run_does_not_read_an_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-key-value")

    def boom(name: str) -> str:
        del name
        raise AssertionError("dry-run read the API key")

    monkeypatch.setattr("canary_leak_bench.cli.key_from_env", boom)
    out = tmp_path / "report.md"
    code = main(
        [
            "run",
            "--dry-run",
            "--out",
            str(out),
            "--scenarios",
            str(ROOT / "scenarios" / "catalog.json"),
        ]
    )
    assert code == 0
    assert "sentinel-key-value" not in out.read_text(encoding="utf-8")


def test_dry_run_rejects_a_cap(tmp_path: Path) -> None:
    code = main(["run", "--dry-run", "--cap-usd", "1", "--out", str(tmp_path / "r.md")])
    assert code == 2
