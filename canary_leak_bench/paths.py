"""Forum paths. One leading slash, no empty segment, no scheme-relative '//'."""

from __future__ import annotations

import re
from typing import TypeGuard

_PATH = re.compile(r"/(?:[A-Za-z0-9._-]{1,64})(?:/[A-Za-z0-9._-]{1,64}){0,8}\Z")


def is_safe_path(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or _PATH.fullmatch(value) is None:
        return False
    parts = value.split("/")
    return parts[0] == "" and all(part not in {"", ".", ".."} for part in parts[1:])
