"""Cost cap. The runner thread is the only thread that touches a Ledger."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from canary_leak_bench.errors import ConfigError

_QUANTUM = Decimal("0.000001")
# What a later turn is allowed to add to the next request: the assistant message
# we are willing to resend, plus one tool result. agent.py enforces both caps.
ASSISTANT_MESSAGE_CAP = 65536
TOOL_RESULT_CAP = 8192


def money(value: Decimal) -> str:
    return format(value.quantize(_QUANTUM, rounding=ROUND_CEILING), "f")


def token_cost(tokens: int, usd_per_mtok: Decimal) -> Decimal:
    if tokens < 0:
        raise ConfigError("token count must be >= 0")
    raw = (Decimal(tokens) * usd_per_mtok) / Decimal(1_000_000)
    return raw.quantize(_QUANTUM, rounding=ROUND_CEILING)


def trial_reservation(
    prompt_bytes: int,
    max_turns: int,
    max_output_tokens: int,
    usd_per_mtok_in: Decimal,
    usd_per_mtok_out: Decimal,
) -> Decimal:
    """Upper bound that re-bills the growing context on every later turn.

    Turn t resends the original prompt plus t prior assistant messages (each
    capped at ASSISTANT_MESSAGE_CAP bytes, counted as tokens) and t tool
    results (each capped at TOOL_RESULT_CAP). The live loop enforces both
    caps and executes at most one tool per turn.
    """
    if prompt_bytes < 0 or max_turns < 1 or max_output_tokens < 1:
        raise ConfigError("reservation inputs must be positive")
    total = Decimal("0")
    for turn in range(max_turns):
        input_tokens = prompt_bytes + turn * (ASSISTANT_MESSAGE_CAP + TOOL_RESULT_CAP)
        total += token_cost(input_tokens, usd_per_mtok_in)
        total += token_cost(max_output_tokens, usd_per_mtok_out)
    return total


class Ledger:
    def __init__(self, cap: Decimal) -> None:
        if not cap.is_finite() or cap < 0:
            raise ConfigError("cap must be a finite USD amount >= 0")
        self.cap = cap
        self.spent = Decimal("0")

    def try_reserve(self, amount: Decimal) -> bool:
        if not amount.is_finite() or amount < 0:
            raise ConfigError("reservation must be a finite USD amount >= 0")
        if self.spent + amount > self.cap:
            return False
        self.spent += amount
        return True

    def settle(self, reserved: Decimal, actual: Decimal) -> None:
        """Replace a reservation with the actual charge. Both must be >= 0."""
        if reserved < 0 or actual < 0:
            raise ConfigError("costs must be >= 0")
        self.spent += actual - reserved
        if self.spent < 0:
            self.spent = Decimal("0")
