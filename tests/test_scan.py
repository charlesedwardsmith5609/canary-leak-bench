"""Response scanning does not stop at a fixed number of JSON strings."""

from __future__ import annotations

import json

from canary_leak_bench.canary import new_canary
from canary_leak_bench.paths import is_safe_path
from canary_leak_bench.scan import scan_response


def test_canary_past_256_strings_still_counts() -> None:
    canary = new_canary()
    values = [f"s{i}" for i in range(300)]
    values.append(canary)
    body = json.dumps(
        {
            "content": [
                {"type": "tool_use", "id": "t", "name": "post_reply", "input": {"bits": values}}
            ]
        }
    ).encode()
    tool_hit, prose_hit, tools = scan_response(body, canary)
    assert tool_hit
    assert not prose_hit
    assert tools == 1


def test_scheme_relative_path_is_rejected() -> None:
    assert not is_safe_path("//collect.example.test/inbox")
    assert is_safe_path("/submit")
    assert not is_safe_path("/../etc/passwd")
