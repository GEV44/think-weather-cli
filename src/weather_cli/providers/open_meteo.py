"""Open-Meteo geocoding and forecast adapter.

This is the only module that knows Open-Meteo's field names. It validates the
untrusted JSON and turns it into canonical-metric, timezone-aware domain models.

Timestamps are requested as Unix epochs (``timeformat=unixtime``) and converted
with the response's IANA timezone, so local clock labels stay correct across
daylight-saving transitions instead of reusing a single fixed UTC offset.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from weather_cli.config import FORECAST_DAYS_REQUESTED, FORECAST_URL, GEOCODING_URL
from weather_cli.errors import ProviderError
from weather_cli.http import HttpClient
from weather_cli.models import (
    CurrentConditions,
    DayForecast,
    HourlyPoint,
    Location,
    WeatherReport,
)
from weather_cli.providers import values as v
from weather_cli.providers.base import GeoMatch

log = logging.getLogger(__name__)

GEOCODING_SERVICE = "Open-Meteo geocoding"
FORECAST_SERVICE = "Open-Meteo forecast"

CURRENT_VARS = (
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "weather_code",
    "is_day",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "cloud_cover",
    "pressure_msl",
    "precipitation",
)
HOURLY_VARS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation_probability",
    "weather_code",
    "wind_speed_10m",
    "is_day",
)
DAILY_VARS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "precipitation_sum",
    "wind_speed_10m_max",
    "sunrise",
    "sunset",
)
REQUEST_VERSION = 1
"""Bump when the requested variables change; it is part of the cache key."""

# Units we explicitly request; a response in any other unit is rejected rather
# than silently mixed with canonical metric values.
_EXPECTED_UNITS = {
    "temperature_2m": "°C",
    "apparent_temperature": "°C",
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
    "wind_speed_10m_max": "km/h",
    "precipitation": "mm",
    "precipitation_sum": "mm",
    "pressure_msl": "hPa",
}

# Feature codes for populated places in GeoNames start with "PPL".
_POPULATED_PLACE_PREFIX = "PPL"

_SCRIPT_LANGUAGES = (
    (0x0530, 0x058F, "hy"),  # Armenian
    (0x0400, 0x04FF, "ru"),  # Cyrillic
    (0x10A0, 0x10FF, "ka"),  # Georgian
    (0x0370, 0x03FF, "el"),  # Greek
)


def _malformed(service: str, detail: str) -> ProviderError:
    log.debug("%s malformed response: %s", service, detail)
    return ProviderError(
        f"{service} returned data in an unexpected format.",
        "The service may be changing or having problems; try again later "
        "(run with --verbose for details).",
    )


# --------------------------------------------------------------------------- geocoding


def query_language(name: str) -> str:
    """Pick a geocoder language so non-Latin names (e.g. Armenian) can match."""
    for char in name:
        for low, high, language in _SCRIPT_LANGUAGES:
            if low <= ord(char) <= high:
                return language
    return "en"


def parse_location(item: Any, *, resolved_by: str = "open-meteo-geocoding") -> Location | None:
    """One geocoding result; ``None`` when it lacks a usable name or coordinates."""
    if not isinstance(item, dict):
        return None
    name = v.text(item.get("name"))
    latitude = v.bounded(item.get("latitude"), -90.0, 90.0)
    longitude = v.bounded(item.get("longitude"), -180.0, 180.0)
    if name is None or latitude is None or longitude is None:
        return None
    country_code = v.text(item.get("country_code"))
    population = v.number(item.get("population"))
    provider_id = v.number(item.get("id"))
    return Location(
        name=name,
        latitude=latitude,
        longitude=longitude,
        country=v.text(item.get("country")),
        country_code=country_code.upper() if country_code else None,
        admin1=v.text(item.get("admin1")),
        admin2=v.text(item.get("admin2")),
        timezone=v.text(item.get("timezone")),
        elevation_m=v.number(item.get("elevation")),
        population=int(population) if population and population > 0 else None,
        provider_id=int(provider_id) if provider_id is not None else None,
        resolved_by=resolved_by,
    )


def is_populated_place(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("feature_code", "")).startswith(
        _POPULATED_PLACE_PREFIX
    )


def parse_geocoding(payload: Any) -> list[GeoMatch]:
    """Usable results in provider rank order (Open-Meteo ranks by relevance/population)."""
    if not isinstance(payload, dict):
        raise _malformed(GEOCODING_SERVICE, "top level is not an object")
    results = payload.get("results")
    if results is None:
        return []  # Open-Meteo omits "results" entirely when nothing matches.
    if not isinstance(results, list):
        raise _malformed(GEOCODING_SERVICE, "'results' is not a list")
    parsed = []
    for item in results:
        location = parse_location(item)
        if location is None:
            log.debug("skipping unusable geocoding result")
            continue
        raw_postcodes = item.get("postcodes")
        postcodes = (
            tuple(p for p in raw_postcodes if isinstance(p, str))
            if isinstance(raw_postcodes, list)
            else ()
        )
        parsed.append(GeoMatch(location, is_populated_place(item), postcodes))
    return parsed


class OpenMeteoGeocoder:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def search(self, name: str, country_code: str | None, count: int = 10) -> list[GeoMatch]:
        params: dict[str, str | int] = {
            "name": name,
            "count": count,
            "language": query_language(name),
            "format": "json",
        }
        if country_code:
            params["countryCode"] = country_code
        payload = self._http.get_json(GEOCODING_URL, params, service=GEOCODING_SERVICE)
        return parse_geocoding(payload)


# --------------------------------------------------------------------------- forecast


def forecast_params(location: Location) -> dict[str, str | int | float]:
    return {
        "latitude": round(location.latitude, 5),
        "longitude": round(location.longitude, 5),
        "current": ",".join(CURRENT_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "forecast_days": FORECAST_DAYS_REQUESTED,
        "timezone": "auto",
        "timeformat": "unixtime",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
    }


def _zone(payload: dict[str, Any]) -> tuple[tzinfo, str, str | None]:
    name = v.text(payload.get("timezone"))
    abbreviation = v.text(payload.get("timezone_abbreviation"))
    if name is None:
        raise _malformed(FORECAST_SERVICE, "missing timezone")
    try:
        return ZoneInfo(name), name, abbreviation
    except (ZoneInfoNotFoundError, ValueError):
        offset = v.number(payload.get("utc_offset_seconds"))
        if offset is None:
            raise _malformed(FORECAST_SERVICE, f"unknown timezone {name!r}") from None
        log.warning(
            "Timezone %r is not in this system's tz database; using a fixed UTC offset. "
            "Install the 'tzdata' package for DST-correct times.",
            name,
        )
        return timezone(timedelta(seconds=offset), abbreviation or name), name, abbreviation


def _check_units(block_units: Any, section: str) -> None:
    if not isinstance(block_units, dict):
        return  # Units metadata is informative; absence is not fatal.
    for variable, expected in _EXPECTED_UNITS.items():
        actual = block_units.get(variable)
        if actual is not None and actual != expected:
            raise _malformed(
                FORECAST_SERVICE, f"{section}.{variable} in {actual!r}, expected {expected!r}"
            )


def _times(block: dict[str, Any], section: str) -> list[int]:
    raw = block.get("time")
    if not isinstance(raw, list) or not all(v.epoch(t) is not None for t in raw):
        raise _malformed(FORECAST_SERVICE, f"{section}.time is not a list of unix timestamps")
    return raw


def _series(block: dict[str, Any], name: str, length: int, section: str) -> Sequence[Any]:
    values = block.get(name)
    if isinstance(values, list) and len(values) == length:
        return values
    if values is not None:
        log.debug("%s.%s has unexpected shape; treating as unavailable", section, name)
    return [None] * length


def _required_series(block: dict[str, Any], name: str, length: int, section: str) -> list[Any]:
    values = block.get(name)
    if not isinstance(values, list) or len(values) != length:
        raise _malformed(FORECAST_SERVICE, f"{section}.{name} missing or wrong length")
    return values


def _section(payload: dict[str, Any], name: str) -> dict[str, Any]:
    block = payload.get(name)
    if not isinstance(block, dict):
        raise _malformed(FORECAST_SERVICE, f"missing '{name}' section")
    return block


def _parse_current(payload: dict[str, Any], zone: tzinfo) -> CurrentConditions:
    block = _section(payload, "current")
    _check_units(payload.get("current_units"), "current")
    timestamp = v.epoch(block.get("time"))
    if timestamp is None:
        raise _malformed(FORECAST_SERVICE, "current.time is not a unix timestamp")
    temperature = v.temperature(block.get("temperature_2m"))
    if temperature is None:
        raise _malformed(FORECAST_SERVICE, "current.temperature_2m missing")
    return CurrentConditions(
        time=datetime.fromtimestamp(timestamp, zone),
        temperature_c=temperature,
        weather_code=v.integer_code(block.get("weather_code")),
        apparent_temperature_c=v.temperature(block.get("apparent_temperature")),
        relative_humidity_pct=v.percent(block.get("relative_humidity_2m")),
        wind_speed_kmh=v.non_negative(block.get("wind_speed_10m")),
        wind_direction_deg=v.bounded(block.get("wind_direction_10m"), 0.0, 360.0),
        wind_gusts_kmh=v.non_negative(block.get("wind_gusts_10m")),
        cloud_cover_pct=v.percent(block.get("cloud_cover")),
        pressure_msl_hpa=v.bounded(block.get("pressure_msl"), 800.0, 1100.0),
        precipitation_mm=v.non_negative(block.get("precipitation")),
        is_day=v.flag(block.get("is_day")),
    )


def _parse_hourly(payload: dict[str, Any], zone: tzinfo) -> tuple[HourlyPoint, ...]:
    block = _section(payload, "hourly")
    _check_units(payload.get("hourly_units"), "hourly")
    times = _times(block, "hourly")
    n = len(times)
    temperature = _required_series(block, "temperature_2m", n, "hourly")
    apparent = _series(block, "apparent_temperature", n, "hourly")
    humidity = _series(block, "relative_humidity_2m", n, "hourly")
    rain_chance = _series(block, "precipitation_probability", n, "hourly")
    code = _series(block, "weather_code", n, "hourly")
    wind_speed = _series(block, "wind_speed_10m", n, "hourly")
    is_day = _series(block, "is_day", n, "hourly")
    points = []
    for i, timestamp in enumerate(times):
        value = v.temperature(temperature[i])
        if value is None:
            continue  # A point without temperature is not worth plotting.
        points.append(
            HourlyPoint(
                time=datetime.fromtimestamp(timestamp, zone),
                temperature_c=value,
                weather_code=v.integer_code(code[i]),
                apparent_temperature_c=v.temperature(apparent[i]),
                relative_humidity_pct=v.percent(humidity[i]),
                precipitation_probability_pct=v.percent(rain_chance[i]),
                wind_speed_kmh=v.non_negative(wind_speed[i]),
                is_day=v.flag(is_day[i]),
            )
        )
    return tuple(points)


def _local_datetime(value: Any, zone: tzinfo) -> datetime | None:
    timestamp = v.epoch(value)
    return None if timestamp is None else datetime.fromtimestamp(timestamp, zone)


def _parse_daily(payload: dict[str, Any], zone: tzinfo) -> tuple[DayForecast, ...]:
    block = _section(payload, "daily")
    _check_units(payload.get("daily_units"), "daily")
    times = _times(block, "daily")
    n = len(times)
    t_max = _required_series(block, "temperature_2m_max", n, "daily")
    t_min = _required_series(block, "temperature_2m_min", n, "daily")
    code = _series(block, "weather_code", n, "daily")
    rain_chance = _series(block, "precipitation_probability_max", n, "daily")
    rain_sum = _series(block, "precipitation_sum", n, "daily")
    wind_max = _series(block, "wind_speed_10m_max", n, "daily")
    sunrise = _series(block, "sunrise", n, "daily")
    sunset = _series(block, "sunset", n, "daily")
    days = []
    for i, timestamp in enumerate(times):
        high, low = v.temperature(t_max[i]), v.temperature(t_min[i])
        if high is None or low is None:
            continue
        local_day: date = datetime.fromtimestamp(timestamp, zone).date()
        days.append(
            DayForecast(
                date=local_day,
                temperature_max_c=max(high, low),
                temperature_min_c=min(high, low),
                weather_code=v.integer_code(code[i]),
                precipitation_probability_max_pct=v.percent(rain_chance[i]),
                precipitation_sum_mm=v.non_negative(rain_sum[i]),
                wind_speed_max_kmh=v.non_negative(wind_max[i]),
                sunrise=_local_datetime(sunrise[i], zone),
                sunset=_local_datetime(sunset[i], zone),
            )
        )
    return tuple(days)


def parse_forecast(payload: Any, location: Location, fetched_at: datetime) -> WeatherReport:
    """Validate and normalize a forecast response that requested ``timeformat=unixtime``."""
    if not isinstance(payload, dict):
        raise _malformed(FORECAST_SERVICE, "top level is not an object")
    zone, zone_name, abbreviation = _zone(payload)
    if location.timezone is None:
        location = replace(location, timezone=zone_name)
    return WeatherReport(
        location=location,
        timezone=zone_name,
        timezone_abbreviation=abbreviation,
        current=_parse_current(payload, zone),
        hourly=_parse_hourly(payload, zone),
        daily=_parse_daily(payload, zone),
        fetched_at=fetched_at.astimezone(UTC),
    )


class OpenMeteoForecaster:
    cache_namespace = f"open-meteo-forecast-v{REQUEST_VERSION}"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def fetch(self, location: Location, fetched_at: datetime) -> WeatherReport:
        payload = self._http.get_json(
            FORECAST_URL, forecast_params(location), service=FORECAST_SERVICE
        )
        return parse_forecast(payload, location, fetched_at)
