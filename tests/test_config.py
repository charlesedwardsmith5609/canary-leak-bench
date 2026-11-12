"""Config parsing. Prices are decimal strings; the cap is a positive decimal."""

from __future__ import annotations

from pathlib import Path

import pytest

from canary_leak_bench.config import load_config, parse_cap, parse_config
from canary_leak_bench.errors import ConfigError


def test_prices_must_be_strings() -> None:
    with pytest.raises(ConfigError):
        parse_config(
            {
                "model_id": "m",
                "api_key_env": "ANTHROPIC_API_KEY",
                "usd_per_mtok_in": 3,
                "usd_per_mtok_out": "15.00",
            }
        )


def test_non_loopback_http_is_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_config(
            {
                "model_id": "m",
                "api_key_env": "ANTHROPIC_API_KEY",
                "usd_per_mtok_in": "3.00",
                "usd_per_mtok_out": "15.00",
                "base_url": "http://example.test",
            }
        )


def test_cap_must_be_positive() -> None:
    with pytest.raises(ConfigError):
        parse_cap("0")
    with pytest.raises(ConfigError):
        parse_cap("nan")


def test_example_config_loads() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root / "examples" / "bench.example.json")
    assert cfg.model_id == "placeholder-model-set-me"
    assert cfg.usd_per_mtok_in > 0
