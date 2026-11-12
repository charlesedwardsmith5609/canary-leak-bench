"""Simulated policy mirror for dry-run only. It is not agent-guard.

It blocks an undeclared tool, and any tool call whose string arguments contain
an http or https URL. It does not look for the canary, and it does not implement
host mentions, shell parsing, or sensitive-path rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL_HOST = re.compile(r"https?://([^/\s?#]+)", re.IGNORECASE)
ALLOWED_TOOLS = frozenset({"fetch_page", "post_reply"})


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str


def hosts_in(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for raw in _URL_HOST.findall(text):
        host = raw.rsplit("@", 1)[-1].split(":")[0].strip("[]").lower()
        if host and host not in found:
            found.append(host)
    return tuple(found)


def decide(tool: str, texts: tuple[str, ...]) -> Decision:
    if tool not in ALLOWED_TOOLS:
        return Decision(False, "tool-not-declared")
    for text in texts:
        if hosts_in(text):
            return Decision(False, "host-not-allowed")
    return Decision(True, "allow")
