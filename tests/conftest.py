"""Shared fixtures. Every test runs offline: real sockets are blocked and recorded."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from weather_cli.http import HttpClient
from weather_cli.models import Location

GEOCODE_HOST = "geocoding-api.open-meteo.com"
FORECAST_HOST = "api.open-meteo.com"
ZIP_HOST = "api.zippopotam.us"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "expects_blocked_network: the test deliberately triggers the network guard"
    )


@pytest.fixture(autouse=True)
def network_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[Any]]:
    """Refuse real connections; fail any test that tries one unless it expects it."""
    attempts: list[Any] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:
        attempts.append(args[:1])
        raise ConnectionRefusedError("network access is disabled in tests")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield attempts
    if not request.node.get_closest_marker("expects_blocked_network"):
        assert not attempts, f"test attempted real network access: {attempts}"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("THINK_WEATHER_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("COLUMNS", "100")
    return cache_dir


# --------------------------------------------------------------------------- payloads


def geo_result(
    name: str,
    lat: float,
    lon: float,
    *,
    country: str = "Armenia",
    cc: str = "AM",
    admin1: str | None = None,
    tz: str = "Asia/Yerevan",
    population: int | None = None,
    feature_code: str = "PPL",
    postcodes: list[str] | None = None,
    ident: int = 1,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": ident,
        "name": name,
        "latitude": lat,
        "longitude": lon,
        "elevation": 1000.0,
        "feature_code": feature_code,
        "country_code": cc,
        "timezone": tz,
        "country": country,
    }
    if admin1:
        item["admin1"] = admin1
    if population is not None:
        item["population"] = population
    if postcodes is not None:
        item["postcodes"] = postcodes
    return item


def postal_result(name: str, lat: float, lon: float, cc: str) -> dict[str, Any]:
    return {
        "country abbreviation": cc,
        "places": [{"place name": name, "latitude": str(lat), "longitude": str(lon)}],
    }


YEREVAN = geo_result(
    "Yerevan", 40.17765, 44.5126, admin1="Yerevan", population=1144700,
    feature_code="PPLC", ident=616052,
)  # fmt: skip
BEVERLY_HILLS = geo_result(
    "Beverly Hills", 34.07362, -118.40036, country="United States", cc="US",
    admin1="California", tz="America/Los_Angeles", population=34869,
    feature_code="PPLX", postcodes=["90209", "90210", "90211"], ident=5328041,
)  # fmt: skip
SPRINGFIELDS = [
    geo_result("Springfield", 37.2, -93.3, country="United States", cc="US", admin1="Missouri",
               tz="America/Chicago", population=170188, ident=11),
    geo_result("Springfield", 39.8, -89.6, country="United States", cc="US", admin1="Illinois",
               tz="America/Chicago", population=114394, ident=12),
]  # fmt: skip


def forecast_payload(
    tz: str = "Asia/Yerevan",
    *,
    start: datetime | None = None,
    days: int = 6,
    current_time: datetime | None = None,
    temperature: Callable[[int], float] = lambda i: 10.0 + (i % 24) / 2,
    current_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A realistic ``timeformat=unixtime`` forecast response.

    Hourly data starts at local midnight of ``start`` (like Open-Meteo); hours
    are generated in UTC steps, so DST transitions behave like the real API.
    """
    zone = ZoneInfo(tz)
    start = start or datetime(2026, 10, 30, tzinfo=zone)
    first = start.astimezone(UTC)
    last_local_midnight = (start + timedelta(days=days)).replace(tzinfo=zone)
    count = int((last_local_midnight.astimezone(UTC) - first).total_seconds() // 3600)
    hours = [first + timedelta(hours=i) for i in range(count)]
    day_starts = [
        datetime(start.year, start.month, start.day, tzinfo=zone) + timedelta(days=d)
        for d in range(days)
    ]
    day_starts = [d.replace(tzinfo=zone) for d in day_starts]
    current_time = current_time or start + timedelta(hours=13, minutes=15)
    offset = zone.utcoffset(current_time)
    current = {
        "time": int(current_time.timestamp()),
        "interval": 900,
        "temperature_2m": 14.6,
        "relative_humidity_2m": 61,
        "apparent_temperature": 13.2,
        "weather_code": 3,
        "is_day": 1,
        "wind_speed_10m": 12.4,
        "wind_direction_10m": 280,
        "wind_gusts_10m": 25.0,
        "cloud_cover": 88,
        "pressure_msl": 1018.3,
        "precipitation": 0.0,
    }
    current.update(current_overrides or {})
    temps = [temperature(i) for i in range(count)]
    return {
        "latitude": 40.18,
        "longitude": 44.51,
        "utc_offset_seconds": int(offset.total_seconds()) if offset else 0,
        "timezone": tz,
        "timezone_abbreviation": "GMT+4",
        "elevation": 1000.0,
        "current_units": {"time": "unixtime", "temperature_2m": "°C", "wind_speed_10m": "km/h"},
        "current": current,
        "hourly_units": {"time": "unixtime", "temperature_2m": "°C"},
        "hourly": {
            "time": [int(h.timestamp()) for h in hours],
            "temperature_2m": temps,
            "apparent_temperature": [t - 1 for t in temps],
            "relative_humidity_2m": [60] * count,
            "precipitation_probability": [(i * 7) % 100 for i in range(count)],
            "weather_code": [61 if i % 24 >= 18 else 1 for i in range(count)],
            "wind_speed_10m": [10.0] * count,
            "is_day": [1 if 7 <= (i % 24) < 19 else 0 for i in range(count)],
        },
        "daily_units": {"time": "unixtime", "temperature_2m_max": "°C"},
        "daily": {
            "time": [int(d.timestamp()) for d in day_starts],
            "weather_code": [0, 2, 3, 61, 71, 95][:days],
            "temperature_2m_max": [20.0 + d for d in range(days)],
            "temperature_2m_min": [5.0 + d for d in range(days)],
            "precipitation_probability_max": [0, 10, 20, 80, 90, 100][:days],
            "precipitation_sum": [0.0, 0.0, 0.4, 12.5, 3.0, 20.0][:days],
            "wind_speed_10m_max": [10.0, 12.0, 14.0, 30.0, 25.0, 40.0][:days],
            "sunrise": [int((d + timedelta(hours=7)).timestamp()) for d in day_starts],
            "sunset": [int((d + timedelta(hours=18)).timestamp()) for d in day_starts],
        },
    }


# --------------------------------------------------------------------------- HTTP


@dataclass
class FakeApi:
    """httpx.MockTransport router that records every request."""

    geocode: dict[str, Any] | Callable[[httpx.Request], httpx.Response] = field(
        default_factory=lambda: {"results": [YEREVAN]}
    )
    forecast: dict[str, Any] | Callable[[httpx.Request], httpx.Response] = field(
        default_factory=forecast_payload
    )
    postal: dict[str, Any] | Callable[[httpx.Request], httpx.Response] | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = {
            GEOCODE_HOST: self.geocode,
            FORECAST_HOST: self.forecast,
            ZIP_HOST: self.postal,
        }.get(request.url.host)
        if route is None:
            return httpx.Response(404, json={})
        if callable(route):
            return route(request)
        return httpx.Response(200, json=route)

    def count(self, host: str) -> int:
        return sum(1 for r in self.requests if r.url.host == host)

    def client(self) -> HttpClient:
        return HttpClient(
            httpx.Client(transport=httpx.MockTransport(self.handler)), sleep=lambda _: None
        )


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def patch_http(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """Make the CLI's live service use the fake API instead of the network."""
    import weather_cli.http as http_module

    original = http_module.HttpClient

    def factory(*args: Any, **kwargs: Any) -> HttpClient:
        return original(
            httpx.Client(transport=httpx.MockTransport(api.handler)), sleep=lambda _: None
        )

    monkeypatch.setattr(http_module, "HttpClient", factory)
    return api


def yerevan_location() -> Location:
    return Location(
        "Yerevan", 40.17765, 44.5126, country="Armenia", country_code="AM",
        admin1="Yerevan", timezone="Asia/Yerevan", population=1144700,
    )  # fmt: skip


class Clock:
    """A controllable UTC clock for TTL tests."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)
