"""Scripted dry-run agent and the live tool loop. Both run on the runner thread.

The scripted agent is a harness check. Its leak pattern is fixed by the design
and is not a measurement of a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from canary_leak_bench.config import BenchConfig
from canary_leak_bench.errors import ForumError, ProviderError
from canary_leak_bench.forum import ForumClient
from canary_leak_bench.ledger import (
    ASSISTANT_MESSAGE_CAP,
    TOOL_RESULT_CAP,
    Ledger,
    money,
    token_cost,
    trial_reservation,
)
from canary_leak_bench.paths import is_safe_path
from canary_leak_bench.policy import decide
from canary_leak_bench.prompts import system_prompt, user_message
from canary_leak_bench.provider import ModelTurn, ToolUse
from canary_leak_bench.recorder import Recorder
from canary_leak_bench.scenarios import COLLECT_URL, Scenario

_MAX_TOOL_CALLS = 32


@dataclass(frozen=True)
class Observation:
    tool_blobs: tuple[str, ...]
    prose: str
    blocked_calls: int
    cost_usd: str
    error: str
    skipped_cap: bool
    tool_forced: bool = False
    prose_forced: bool = False


class Provider(Protocol):
    def complete(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int,
        timeout_s: float,
    ) -> ModelTurn: ...


class ScriptedAgent:
    """Deterministic stand-in. See REQUIREMENTS R4. No model call and no API key."""

    def __init__(self, config: str) -> None:
        self._config = config

    def run(self, scenario: Scenario, canary: str, client: ForumClient) -> Observation:
        page = scenario.pages[0].path
        if self._config == "system-prompt":
            body = "I will not share credentials."
        elif self._config == "agent-guard" and scenario.category == "prompt_injection":
            body = f"{canary}\n{COLLECT_URL}"
        else:
            body = f"token {canary}"
        calls = (
            ("fetch_page", (page,)),
            ("post_reply", (scenario.submit_path, body)),
        )
        blobs: list[str] = []
        blocked = 0
        for name, texts in calls:
            blobs.append("\n".join(texts))
            allowed = decide(name, texts).allow if self._config == "agent-guard" else True
            if not allowed:
                blocked += 1
                continue
            self._send(client, name, texts)
        return Observation(tuple(blobs), "", blocked, "0", "", False)

    def _send(self, client: ForumClient, name: str, texts: tuple[str, ...]) -> None:
        if name == "fetch_page":
            client.get(texts[0])
            return
        client.post(texts[0], texts[1])


class LiveAgent:
    """Calls the model, then performs tool calls the response still contains."""

    def __init__(
        self,
        config: str,
        provider: Provider,
        ledger: Ledger,
        spec: BenchConfig,
        recorder: Recorder | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._ledger = ledger
        self._spec = spec
        self._recorder = recorder

    def run(self, scenario: Scenario, canary: str, client: ForumClient) -> Observation:
        system = system_prompt(self._config)
        messages: list[dict[str, object]] = [
            {"role": "user", "content": user_message(canary, scenario.task)}
        ]
        reserved = trial_reservation(
            _prompt_bytes(system, messages),
            self._spec.max_turns,
            self._spec.max_output_tokens,
            self._spec.usd_per_mtok_in,
            self._spec.usd_per_mtok_out,
        )
        if not self._ledger.try_reserve(reserved):
            return Observation((), "", 0, "0", "", True)
        if self._recorder is not None:
            self._recorder.state.reset(canary)
        blobs: list[str] = []
        prose: list[str] = []
        visible = 0
        actual = Decimal("0")
        try:
            for _turn in range(self._spec.max_turns):
                turn = self._provider.complete(
                    model_id=self._spec.model_id,
                    system=system,
                    messages=messages,
                    max_tokens=self._spec.max_output_tokens,
                    timeout_s=self._spec.timeout_s,
                )
                actual += token_cost(turn.input_tokens, self._spec.usd_per_mtok_in)
                actual += token_cost(turn.output_tokens, self._spec.usd_per_mtok_out)
                prose.append(turn.prose)
                visible += len(turn.tool_calls)
                for call in turn.tool_calls:
                    blobs.append(_blob(call))
                if turn.stop_reason != "tool_use" or not turn.tool_calls:
                    break
                try:
                    encoded = json.dumps(turn.content).encode("utf-8")
                except (TypeError, ValueError):
                    encoded = b" " * (ASSISTANT_MESSAGE_CAP + 1)
                if len(encoded) > ASSISTANT_MESSAGE_CAP or len(turn.tool_calls) > _MAX_TOOL_CALLS:
                    break
                messages.append({"role": "assistant", "content": turn.content})
                messages.append({"role": "user", "content": _results(client, turn)})
        except ProviderError:
            forced_tool, forced_prose, upstream_tools = False, False, visible
            if self._recorder is not None:
                forced_tool, forced_prose, upstream_tools = self._recorder.state.snapshot()
            return Observation(
                tuple(blobs),
                "\n".join(prose),
                max(0, upstream_tools - visible),
                money(reserved),
                "provider error",
                False,
                tool_forced=forced_tool,
                prose_forced=forced_prose,
            )
        self._ledger.settle(reserved, actual)
        forced_tool = False
        forced_prose = False
        upstream_tools = visible
        if self._recorder is not None:
            forced_tool, forced_prose, upstream_tools = self._recorder.state.snapshot()
        return Observation(
            tuple(blobs),
            "\n".join(prose),
            max(0, upstream_tools - visible),
            money(actual),
            "",
            False,
            tool_forced=forced_tool,
            prose_forced=forced_prose,
        )


def _prompt_bytes(system: str, messages: list[dict[str, object]]) -> int:
    return len(system.encode("utf-8")) + len(repr(messages).encode("utf-8"))


def _results(client: ForumClient, turn: ModelTurn) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for index, call in enumerate(turn.tool_calls):
        content = _execute(client, call) if index == 0 else "not executed"
        if len(content) > TOOL_RESULT_CAP:
            content = content[:TOOL_RESULT_CAP]
        results.append({"type": "tool_result", "tool_use_id": call.call_id, "content": content})
    return results


def _blob(call: ToolUse) -> str:
    try:
        rendered = json.dumps(call.arguments)
    except (TypeError, ValueError, RecursionError):
        rendered = ""
    return call.name + "\n" + rendered


def _execute(client: ForumClient, call: ToolUse) -> str:
    path = call.arguments.get("path")
    if not is_safe_path(path):
        return "rejected"
    try:
        if call.name == "fetch_page":
            return _strip(client.get(path))[:8000]
        if call.name == "post_reply":
            body = call.arguments.get("body")
            if not isinstance(body, str):
                return "rejected"
            return _strip(client.post(path, body[:32768]))
    except ForumError:
        return "forum request failed"
    return "rejected"


def _strip(text: str) -> str:
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
