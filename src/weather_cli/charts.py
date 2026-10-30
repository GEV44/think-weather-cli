"""Tiny text charts: sparklines, range bars and column charts.

Pure string functions with a Unicode and an ASCII alphabet. They tolerate flat
series (all values equal), negative values, missing points (``None``) and very
small widths, so callers never need special cases.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

SPARK_UNICODE = "▁▂▃▄▅▆▇█"
SPARK_ASCII = "_.-:=+*#"
EIGHTHS = " ▁▂▃▄▅▆▇█"


def _span(values: Sequence[float | None]) -> tuple[float, float] | None:
    present = [v for v in values if v is not None and math.isfinite(v)]
    return (min(present), max(present)) if present else None


def level(value: float, low: float, high: float, steps: int) -> int:
    """Map ``value`` in ``[low, high]`` to ``0..steps-1``; a flat range maps to the middle."""
    if steps <= 1:
        return 0
    if high - low < 1e-9:
        return (steps - 1) // 2
    ratio = (value - low) / (high - low)
    return max(0, min(steps - 1, round(ratio * (steps - 1))))


def sparkline(
    values: Sequence[float | None],
    *,
    ascii_only: bool = False,
    span: tuple[float, float] | None = None,
) -> str:
    """One character per value; missing values become spaces.

    By default the series is scaled to its own min..max; pass ``span`` for an
    absolute scale (e.g. ``(0, 100)`` for percentages).
    """
    alphabet = SPARK_ASCII if ascii_only else SPARK_UNICODE
    span = span or _span(values)
    if span is None:
        return " " * len(values)
    low, high = span
    return "".join(
        " " if v is None else alphabet[level(v, low, high, len(alphabet))] for v in values
    )


def range_bar(
    low: float,
    high: float,
    span_low: float,
    span_high: float,
    width: int,
    *,
    ascii_only: bool = False,
) -> str:
    """A horizontal bar from ``low`` to ``high`` positioned within the overall span."""
    if width <= 0:
        return ""
    track, fill = (" ", "=") if ascii_only else ("·", "━")
    start = level(low, span_low, span_high, width)
    end = level(high, span_low, span_high, width)
    if span_high - span_low < 1e-9:
        start, end = 0, width - 1
    start, end = min(start, end), max(start, end)
    return track * start + fill * (end - start + 1) + track * (width - end - 1)


def column_chart(
    values: Sequence[float | None], height: int, *, ascii_only: bool = False
) -> list[str]:
    """Rows (top to bottom) of a vertical bar chart, one column per value.

    Unicode mode uses eighth blocks for sub-row precision. The lowest value
    still gets a visible sliver so a column is never mistaken for missing data.
    """
    height = max(1, height)
    span = _span(values)
    if span is None:
        return [" " * len(values) for _ in range(height)]
    low, high = span
    resolution = 8
    total = height * resolution
    levels = [
        None if v is None else max(1, level(v, low, high, total + 1)) if high > low else total // 2
        for v in values
    ]
    rows = []
    for row in range(height):
        floor = (height - row - 1) * resolution
        cells = []
        for lvl in levels:
            if lvl is None:
                cells.append(" ")
                continue
            filled = max(0, min(resolution, lvl - floor))
            if ascii_only:
                cells.append("#" if filled >= resolution // 2 else " ")
            else:
                cells.append(EIGHTHS[filled])
        rows.append("".join(cells))
    return rows
