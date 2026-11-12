"""Fake canaries. Generation uses secrets; matching is an exact substring."""

from __future__ import annotations

import re
import secrets

CANARY_RE = re.compile(r"clb_[0-9a-f]{32}\Z")
REDACTED = "[CANARY]"


def new_canary() -> str:
    """Return a fresh fake credential. It is not a key for any real service."""
    return "clb_" + secrets.token_hex(16)


def is_canary(value: str) -> bool:
    return CANARY_RE.fullmatch(value) is not None


def contains(text: str, canary: str) -> bool:
    if not is_canary(canary):
        raise ValueError("refusing to scan for a value that is not a canary")
    return canary in text


def contains_bytes(data: bytes, canary: str) -> bool:
    if not is_canary(canary):
        raise ValueError("refusing to scan for a value that is not a canary")
    return canary.encode("ascii") in data


def redact(text: str, canary: str) -> str:
    if not is_canary(canary):
        raise ValueError("refusing to redact with a value that is not a canary")
    return text.replace(canary, REDACTED)
