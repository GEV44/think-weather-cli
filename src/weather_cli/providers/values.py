"""Defensive readers for untrusted JSON scalars: wrong types become ``None``."""

from __future__ import annotations

import math
from typing import Any


def number(value: Any) -> float | None:
    """A finite JSON number as float; anything else (bool, str, NaN, null) is None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def bounded(value: Any, low: float, high: float) -> float | None:
    result = number(value)
    return result if result is not None and low <= result <= high else None


def integer_code(value: Any) -> int | None:
    result = number(value)
    return int(result) if result is not None and result.is_integer() else None


def flag(value: Any) -> bool | None:
    result = number(value)
    return None if result is None else result != 0


def text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


MIN_EPOCH = 0  # 1970-01-01
MAX_EPOCH = 4_102_444_800  # 2100-01-01: anything later is corrupt data, not a forecast


def epoch(value: Any) -> int | None:
    """A plausible Unix timestamp in seconds; ``None`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_EPOCH < value < MAX_EPOCH else None


def temperature(value: Any) -> float | None:
    return bounded(value, -100.0, 70.0)


def percent(value: Any) -> float | None:
    return bounded(value, 0.0, 100.0)


def non_negative(value: Any) -> float | None:
    return bounded(value, 0.0, math.inf)
