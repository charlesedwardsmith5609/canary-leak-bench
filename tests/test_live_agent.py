"""Live loop against an in-process provider. No DNS and no API key."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from canary_leak_bench.agent import LiveAgent
from canary_leak_bench.config import BenchConfig
from canary_leak_bench.forum import ForumClient
from canary_leak_bench.ledger import Ledger
from canary_leak_bench.provider import ModelTurn, ToolUse
from canary_leak_bench.runner import run_trials
from canary_leak_bench.scenarios import load_catalog

_CANARY = re.compile(r"clb_[0-9a-f]{32}")
ROOT = Path(__file__).resolve().parents[1]


class _Echo:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        model_id: str,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int,
        timeout_s: float,
    ) -> ModelTurn:
        del model_id, system, max_tokens, timeout_s
        self.calls += 1
        if self.calls > 1:
            return ModelTurn([], (), "done", 1, 1, "end_turn")
        blob = repr(messages)
        match = _CANARY.search(blob)
        token = match.group(0) if match else "missing"
        call = ToolUse("t1", "post_reply", {"path": "/submit", "body": token})
        return ModelTurn(
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "post_reply",
                    "input": {"path": "/submit", "body": token},
                }
            ],
            (call,),
            "",
            2,
            2,
            "tool_use",
        )


def _spec(cap_prices: str = "3") -> BenchConfig:
    return BenchConfig(
        model_id="fake-model",
        api_key_env="ANTHROPIC_API_KEY",
        usd_per_mtok_in=Decimal(cap_prices),
        usd_per_mtok_out=Decimal(cap_prices),
        max_output_tokens=64,
        timeout_s=2,
        max_turns=2,
        base_url="https://api.anthropic.com",
    )


def test_live_agent_posts_what_the_model_returned() -> None:
    from canary_leak_bench.ledger import Ledger

    scenario = load_catalog(ROOT / "scenarios" / "catalog.json")[0]
    echo = _Echo()

    def make(config: str) -> LiveAgent:
        del config
        return LiveAgent("baseline", echo, Ledger(Decimal("50")), _spec())

    results = run_trials((scenario,), ("baseline",), make, simulated=False)
    assert echo.calls == 2
    assert results[0].status == "ok"
    assert results[0].server_hit
    assert results[0].tool_hit
    assert "clb_" not in results[0].scenario_id


def test_provider_error_keeps_the_reservation() -> None:
    scenario = load_catalog(ROOT / "scenarios" / "catalog.json")[0]
    ledger = Ledger(Decimal("50"))

    class Boom:
        def complete(
            self,
            *,
            model_id: str,
            system: str,
            messages: list[dict[str, object]],
            max_tokens: int,
            timeout_s: float,
        ) -> ModelTurn:
            del model_id, system, messages, max_tokens, timeout_s
            from canary_leak_bench.errors import ProviderError

            raise ProviderError("provider error")

    def make(config: str) -> LiveAgent:
        del config
        return LiveAgent("baseline", Boom(), ledger, _spec())

    results = run_trials((scenario,), ("baseline",), make, simulated=False)
    assert results[0].status == "error"
    assert not results[0].leaked
    assert ledger.spent > 0
    assert results[0].cost_usd != "0"


def test_a_post_that_raises_afterwards_still_counts_as_a_leak() -> None:
    scenario = load_catalog(ROOT / "scenarios" / "catalog.json")[0]

    class PostsThenRaises:
        def run(self, scenario: object, canary: str, client: ForumClient) -> None:
            del scenario
            client.post("/submit", canary)
            raise RuntimeError("after the post")

    results = run_trials(
        (scenario,), ("baseline",), lambda config: PostsThenRaises(), simulated=False
    )
    assert results[0].status == "ok"
    assert results[0].leaked
    assert results[0].server_hit


def test_cap_skips_before_any_call() -> None:
    scenario = load_catalog(ROOT / "scenarios" / "catalog.json")[0]
    echo = _Echo()
    ledger = Ledger(Decimal("0.000001"))

    def make(config: str) -> LiveAgent:
        del config
        return LiveAgent("baseline", echo, ledger, _spec())

    results = run_trials((scenario,), ("baseline",), make, simulated=False)
    assert echo.calls == 0
    assert results[0].status == "skipped_cap"
    assert not results[0].leaked
