"""Anthropic Messages client. Synchronous. The API key never appears in errors.

Threading: the caller is the runner thread. Each call starts one watchdog timer
thread whose only job is to shut the socket down at the deadline.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from canary_leak_bench.errors import ProviderError
from canary_leak_bench.http_post import post_bytes

_MAX_TOKENS = 10**9

_TOOLS: list[dict[str, object]] = [
    {
        "name": "fetch_page",
        "description": "Fetch a forum page by absolute path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "post_reply",
        "description": "Post a reply to an absolute forum path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["path", "body"],
        },
    },
]


@dataclass(frozen=True)
class ToolUse:
    call_id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ModelTurn:
    content: list[dict[str, object]]
    tool_calls: tuple[ToolUse, ...]
    prose: str
    input_tokens: int
    output_tokens: int
    stop_reason: str


def usable_key(key: str) -> str:
    if not key or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise ProviderError("API key is missing or not usable")
    return key


def key_from_env(name: str) -> str:
    if not name or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in name):
        raise ProviderError("API key environment variable name is not usable")
    return usable_key(os.environ.get(name, ""))


class AnthropicProvider:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._key = usable_key(api_key)
        self._base = base_url

    def complete(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int,
        timeout_s: float,
    ) -> ModelTurn:
        body = json.dumps(
            {
                "model": model_id,
                "max_tokens": max_tokens,
                "system": system,
                "tools": _TOOLS,
                "messages": messages,
            }
        ).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "x-api-key": self._key,
            "anthropic-version": "2023-06-01",
        }
        status, data = post_bytes(self._base, "/v1/messages", headers, body, timeout_s)
        if status >= 400:
            raise ProviderError(f"HTTP {status} from provider")
        return parse_turn(data)


def parse_turn(data: bytes) -> ModelTurn:
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise ProviderError("response is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("response is not a JSON object")
    content = parsed.get("content")
    usage = parsed.get("usage")
    if not isinstance(content, list) or not isinstance(usage, dict):
        raise ProviderError("response missing content or usage")
    blocks: list[dict[str, object]] = []
    calls: list[ToolUse] = []
    prose: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        blocks.append({str(key): value for key, value in block.items()})
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            prose.append(block["text"])
        elif kind == "tool_use":
            name = block.get("name")
            call_id = block.get("id")
            raw_input = block.get("input")
            if isinstance(name, str) and isinstance(call_id, str):
                arguments = (
                    {str(key): value for key, value in raw_input.items()}
                    if isinstance(raw_input, dict)
                    else {}
                )
                calls.append(ToolUse(call_id, name, arguments))
    stop = parsed.get("stop_reason")
    return ModelTurn(
        content=blocks,
        tool_calls=tuple(calls),
        prose="\n".join(prose),
        input_tokens=_count(usage.get("input_tokens"), "input_tokens"),
        output_tokens=_count(usage.get("output_tokens"), "output_tokens"),
        stop_reason=stop if isinstance(stop, str) else "",
    )


def _count(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_TOKENS:
        raise ProviderError(f"response has invalid {what}")
    return value
