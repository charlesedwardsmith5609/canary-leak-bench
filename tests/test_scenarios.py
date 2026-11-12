"""Catalog rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from canary_leak_bench.errors import ScenarioError
from canary_leak_bench.scenarios import CATEGORIES, COLLECT_URL, load_catalog

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "scenarios" / "catalog.json"


def test_catalog_has_twelve_scenarios_three_per_category() -> None:
    scenarios = load_catalog(CATALOG)
    assert len(scenarios) == 12
    for category in CATEGORIES:
        assert sum(1 for s in scenarios if s.category == category) == 3


def test_collect_url_is_only_on_injection_pages() -> None:
    for scenario in load_catalog(CATALOG):
        text = "\n".join(page.body for page in scenario.pages)
        if scenario.category == "prompt_injection":
            assert COLLECT_URL in text
            assert "collect.example.test" not in scenario.task
        else:
            assert COLLECT_URL not in text


def test_catalog_rejects_a_canary_prefix(tmp_path: Path) -> None:
    raw = CATALOG.read_text(encoding="utf-8").replace("crash on startup", "clb_notreally")
    path = tmp_path / "catalog.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_catalog(path)
