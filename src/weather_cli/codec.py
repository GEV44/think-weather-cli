"""Lossless JSON (de)serialization of domain models for the cache.

Datetimes are stored as ISO 8601 with their UTC offset; on load they are
re-attached to the report's IANA zone so DST-aware labels survive a round trip.
Any structural problem raises :class:`CodecError`, which the service treats as
a cache miss.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import date, datetime, tzinfo
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weather_cli.models import (
    CurrentConditions,
    DayForecast,
    HourlyPoint,
    Location,
    WeatherReport,
)
from weather_cli.providers.base import GeoMatch

FORMAT_VERSION = 1

T = TypeVar("T")


class CodecError(ValueError):
    pass


def _default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value).__name__}")


def dumps_report(report: WeatherReport) -> str:
    return json.dumps({"v": FORMAT_VERSION, "report": asdict(report)}, default=_default)


def dumps_matches(matches: list[GeoMatch]) -> str:
    return json.dumps({"v": FORMAT_VERSION, "matches": [asdict(m) for m in matches]})


def _zone(name: str) -> tzinfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _build(cls: type[T], data: Any, zone: tzinfo | None = None) -> T:
    """Construct a flat dataclass, converting ISO strings for date/datetime fields."""
    if not isinstance(data, dict):
        raise CodecError(f"{cls.__name__}: expected object")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in data:
            continue
        value = data[f.name]
        kind = str(f.type)
        if isinstance(value, str) and "datetime" in kind:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise CodecError(f"{cls.__name__}.{f.name}: naive datetime")
            value = parsed.astimezone(zone) if zone else parsed
        elif isinstance(value, str) and kind.startswith("date"):
            value = date.fromisoformat(value)
        kwargs[f.name] = value
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise CodecError(str(exc)) from exc


def _envelope(text: str, key: str) -> Any:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CodecError("invalid JSON") from exc
    if not isinstance(data, dict) or data.get("v") != FORMAT_VERSION or key not in data:
        raise CodecError("unknown cache format")
    return data[key]


def loads_report(text: str) -> WeatherReport:
    data = _envelope(text, "report")
    if not isinstance(data, dict):
        raise CodecError("report: expected object")
    try:
        zone = _zone(str(data["timezone"]))
        report = WeatherReport(
            location=_build(Location, data["location"]),
            timezone=str(data["timezone"]),
            timezone_abbreviation=data.get("timezone_abbreviation"),
            current=_build(CurrentConditions, data["current"], zone),
            hourly=tuple(_build(HourlyPoint, h, zone) for h in data["hourly"]),
            daily=tuple(_build(DayForecast, d, zone) for d in data["daily"]),
            fetched_at=_build_datetime(data["fetched_at"]),
            provider=str(data.get("provider", "Open-Meteo")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CodecError(f"report: {exc}") from exc
    return report


def _build_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CodecError("expected ISO datetime string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise CodecError("naive datetime")
    return parsed


def loads_matches(text: str) -> list[GeoMatch]:
    data = _envelope(text, "matches")
    if not isinstance(data, list):
        raise CodecError("matches: expected list")
    matches = []
    try:
        for item in data:
            if not isinstance(item, dict):
                raise CodecError("match: expected object")
            matches.append(
                GeoMatch(
                    location=_build(Location, item["location"]),
                    populated_place=bool(item.get("populated_place", True)),
                    postcodes=tuple(str(p) for p in item.get("postcodes", ())),
                )
            )
    except (KeyError, TypeError) as exc:
        raise CodecError(f"matches: {exc}") from exc
    return matches
