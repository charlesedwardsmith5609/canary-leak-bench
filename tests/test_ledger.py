"""Cost cap accounting. The runner thread is the only caller; these tests are single-threaded."""

from __future__ import annotations

from decimal import Decimal

import pytest

from canary_leak_bench.errors import ConfigError
from canary_leak_bench.ledger import Ledger, token_cost, trial_reservation


def test_reserve_past_the_cap_does_not_spend() -> None:
    ledger = Ledger(Decimal("1"))
    assert ledger.try_reserve(Decimal("0.40"))
    assert not ledger.try_reserve(Decimal("0.70"))
    assert ledger.spent == Decimal("0.40")


def test_settle_replaces_the_reservation() -> None:
    ledger = Ledger(Decimal("1"))
    assert ledger.try_reserve(Decimal("0.50"))
    ledger.settle(Decimal("0.50"), Decimal("0.10"))
    assert ledger.spent == Decimal("0.10")
    assert ledger.try_reserve(Decimal("0.80"))


def test_kept_reservation_still_counts_after_an_error() -> None:
    ledger = Ledger(Decimal("1"))
    assert ledger.try_reserve(Decimal("0.90"))
    # An error leaves the reservation in place by not calling settle.
    assert not ledger.try_reserve(Decimal("0.20"))
    assert ledger.spent == Decimal("0.90")


def test_token_cost_rounds_up() -> None:
    assert token_cost(1, Decimal("0.01")) == Decimal("0.000001")


def test_trial_reservation_rebills_growing_context() -> None:
    from canary_leak_bench.ledger import ASSISTANT_MESSAGE_CAP, TOOL_RESULT_CAP

    amount = trial_reservation(100, 4, 512, Decimal("3"), Decimal("15"))
    total = Decimal("0")
    for turn in range(4):
        total += token_cost(100 + turn * (ASSISTANT_MESSAGE_CAP + TOOL_RESULT_CAP), Decimal("3"))
        total += token_cost(512, Decimal("15"))
    assert amount == total


def test_cap_must_be_finite() -> None:
    with pytest.raises(ConfigError):
        Ledger(Decimal("NaN"))
