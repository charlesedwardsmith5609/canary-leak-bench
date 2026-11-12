"""Simulated mirror. It blocks hosts and undeclared tools, not the canary itself."""

from __future__ import annotations

from canary_leak_bench.canary import new_canary
from canary_leak_bench.policy import decide


def test_canary_alone_is_allowed() -> None:
    canary = new_canary()
    decision = decide("post_reply", ("/submit", f"token {canary}"))
    assert decision.allow
    assert decision.rule == "allow"


def test_off_forum_url_is_blocked() -> None:
    decision = decide("post_reply", ("/submit", "https://collect.example.test/inbox"))
    assert not decision.allow
    assert decision.rule == "host-not-allowed"


def test_undeclared_tool_is_blocked() -> None:
    decision = decide("Bash", ("ls",))
    assert not decision.allow
    assert decision.rule == "tool-not-declared"
