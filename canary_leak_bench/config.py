"""Live-run configuration. Prices are decimal strings supplied by the operator."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from canary_leak_bench.errors import ConfigError, ProviderError
from canary_leak_bench.http_post import check_base_url


@dataclass(frozen=True)
class BenchConfig:
    model_id: str
    api_key_env: str
    usd_per_mtok_in: Decimal
    usd_per_mtok_out: Decimal
    max_output_tokens: int = 512
    timeout_s: float = 60.0
    max_turns: int = 4
    base_url: str = "https://api.anthropic.com"


def _money(data: dict[str, object], key: str) -> Decimal:
    value = data.get(key)
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a decimal string, not a JSON number")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ConfigError(f"{key} is not a decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise ConfigError(f"{key} must be > 0")
    return amount


def _int(data: dict[str, object], key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    if not low <= value <= high:
        raise ConfigError(f"{key} must be between {low} and {high}")
    return value


def parse_config(data: object) -> BenchConfig:
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    model_id = data.get("model_id")
    env_name = data.get("api_key_env")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ConfigError("model_id must be a non-empty string")
    if not isinstance(env_name, str) or not env_name.strip():
        raise ConfigError("api_key_env must be a non-empty string")
    timeout = data.get("timeout_s", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise ConfigError("timeout_s must be a number")
    timeout_f = float(timeout)
    if not math.isfinite(timeout_f) or timeout_f <= 0 or timeout_f > 600:
        raise ConfigError("timeout_s must be between 0 and 600")
    base = data.get("base_url", "https://api.anthropic.com")
    if not isinstance(base, str):
        raise ConfigError("base_url must be a string")
    try:
        base_url = check_base_url(base)
    except ProviderError as exc:
        raise ConfigError(str(exc)) from None
    return BenchConfig(
        model_id=model_id,
        api_key_env=env_name,
        usd_per_mtok_in=_money(data, "usd_per_mtok_in"),
        usd_per_mtok_out=_money(data, "usd_per_mtok_out"),
        max_output_tokens=_int(data, "max_output_tokens", 512, 1, 8192),
        timeout_s=timeout_f,
        max_turns=_int(data, "max_turns", 4, 1, 8),
        base_url=base_url,
    )


def load_config(path: Path) -> BenchConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config: {type(exc).__name__}") from None
    return parse_config(raw)


def parse_cap(text: str) -> Decimal:
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ConfigError("--cap-usd is not a decimal") from exc
    if not amount.is_finite() or amount <= 0:
        raise ConfigError("--cap-usd must be > 0")
    return amount
