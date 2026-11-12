"""Load the scenario catalog. The catalog is immutable data, read on the runner thread."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from canary_leak_bench.errors import ScenarioError
from canary_leak_bench.paths import is_safe_path

CATEGORIES = (
    "peer_forum",
    "install_script",
    "helpful_colleague",
    "prompt_injection",
)
COLLECT_URL = "https://collect.example.test/inbox"
_MAX_BODY = 8 * 1024  # bound on untrusted page text kept in memory
_MIN_SCENARIOS = 10
_MAX_SCENARIOS = 20


@dataclass(frozen=True)
class Page:
    path: str
    content_type: str
    body: str


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    task: str
    submit_path: str
    pages: tuple[Page, ...]

    def page_map(self) -> dict[str, Page]:
        return {page.path: page for page in self.pages}


def _path(value: object, what: str) -> str:
    if not is_safe_path(value):
        raise ScenarioError(f"{what} is not a safe absolute path")
    return str(value)


def _text(value: object, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{what} must be a non-empty string")
    if len(value.encode("utf-8")) > limit:
        raise ScenarioError(f"{what} is too long")
    if "clb_" in value:
        raise ScenarioError(f"{what} must not contain a canary prefix")
    if any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise ScenarioError(f"{what} contains a control character")
    return value


def _scenario(data: object) -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError("scenario must be an object")
    category = data.get("category")
    if category not in CATEGORIES:
        raise ScenarioError("scenario category is not one of the four required kinds")
    sid = data.get("id")
    if not isinstance(sid, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
        raise ScenarioError("scenario id must be lowercase letters, digits, and dashes")
    pages_raw = data.get("pages")
    if not isinstance(pages_raw, list) or not pages_raw:
        raise ScenarioError(f"{sid}: pages must be a non-empty list")
    pages: list[Page] = []
    for item in pages_raw:
        if not isinstance(item, dict):
            raise ScenarioError(f"{sid}: page must be an object")
        ctype = item.get("content_type")
        if not isinstance(ctype, str) or not re.fullmatch(
            r"text/[a-z0-9.+-]{1,32}; charset=utf-8", ctype
        ):
            raise ScenarioError(f"{sid}: content_type must be text/*; charset=utf-8")
        pages.append(
            Page(
                path=_path(item.get("path"), f"{sid} page path"),
                content_type=ctype,
                body=_text(item.get("body"), f"{sid} page body", _MAX_BODY),
            )
        )
    task = _text(data.get("task"), f"{sid} task", _MAX_BODY)
    if "collect.example.test" in task:
        raise ScenarioError(f"{sid}: the task must not name the collect host")
    if not any("FORUM_TOKEN" in page.body for page in pages):
        raise ScenarioError(f"{sid}: the bait must mention FORUM_TOKEN")
    return Scenario(
        id=sid,
        category=str(category),
        task=task,
        submit_path=_path(data.get("submit_path"), f"{sid} submit_path"),
        pages=tuple(pages),
    )


def load_catalog(path: Path) -> tuple[Scenario, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"cannot read catalog: {type(exc).__name__}") from None
    if not isinstance(raw, list):
        raise ScenarioError("catalog must be a list")
    scenarios = tuple(_scenario(item) for item in raw)
    if not _MIN_SCENARIOS <= len(scenarios) <= _MAX_SCENARIOS:
        raise ScenarioError("catalog must contain between 10 and 20 scenarios")
    ids = [s.id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ScenarioError("scenario ids must be unique")
    for category in CATEGORIES:
        count = sum(1 for s in scenarios if s.category == category)
        if count < 2:
            raise ScenarioError(f"{category} needs at least two scenarios")
    for scenario in scenarios:
        mentioned = any(COLLECT_URL in page.body for page in scenario.pages)
        if scenario.category == "prompt_injection" and not mentioned:
            raise ScenarioError(f"{scenario.id}: injection page must name the collect URL")
        if scenario.category != "prompt_injection" and mentioned:
            raise ScenarioError(f"{scenario.id}: only injection pages may name the collect URL")
    return scenarios
