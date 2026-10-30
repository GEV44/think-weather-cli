"""Open-Meteo adapter: request shape and defensive parsing of untrusted JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from conftest import (
    FORECAST_HOST,
    GEOCODE_HOST,
    YEREVAN,
    FakeApi,
    forecast_payload,
    yerevan_location,
)
from weather_cli.errors import ProviderError
from weather_cli.providers.open_meteo import (
    CURRENT_VARS,
    OpenMeteoForecaster,
    OpenMeteoGeocoder,
    parse_forecast,
    parse_geocoding,
    query_language,
)

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- geocoding


def test_geocoder_sends_documented_parameters(api: FakeApi) -> None:
    OpenMeteoGeocoder(api.client()).search("Yerevan", "AM")
    request = api.requests[0]
    assert request.url.host == GEOCODE_HOST
    assert request.url.scheme == "https"
    assert request.method == "GET"
    params = request.url.params
    assert params["name"] == "Yerevan"
    assert params["countryCode"] == "AM"  # not "country"
    assert params["language"] == "en"
    assert params["count"] == "10"


def test_geocoder_omits_country_when_not_given(api: FakeApi) -> None:
    OpenMeteoGeocoder(api.client()).search("Yerevan", None)
    assert "countryCode" not in api.requests[0].url.params


def test_postcode_is_sent_verbatim_with_leading_zeros(api: FakeApi) -> None:
    OpenMeteoGeocoder(api.client()).search("00501", "US")
    assert api.requests[0].url.params["name"] == "00501"


@pytest.mark.parametrize(("name", "language"), [("Երևան", "hy"), ("Москва", "ru"), ("Paris", "en")])
def test_query_language_follows_script(name: str, language: str) -> None:
    assert query_language(name) == language


def test_parse_geocoding_maps_fields() -> None:
    [match] = parse_geocoding({"results": [YEREVAN]})
    location = match.location
    assert location.name == "Yerevan"
    assert location.country_code == "AM"
    assert location.timezone == "Asia/Yerevan"
    assert location.population == 1144700
    assert location.provider_id == 616052
    assert match.populated_place


def test_missing_results_key_means_no_matches() -> None:
    assert parse_geocoding({"generationtime_ms": 0.1}) == []


@pytest.mark.parametrize("payload", [[], "oops", None, {"results": "nope"}])
def test_wrong_top_level_types_are_provider_errors(payload: Any) -> None:
    with pytest.raises(ProviderError, match="unexpected format"):
        parse_geocoding(payload)


def test_unusable_results_are_skipped() -> None:
    results = [
        {"name": "No coordinates"},
        {"name": "Bad lat", "latitude": 123, "longitude": 0},
        {"name": "", "latitude": 1, "longitude": 1},
        "not-an-object",
        {"name": "Good", "latitude": "40", "longitude": 44},  # string coordinate rejected
        {"name": "Fine", "latitude": 40.0, "longitude": 44.0},
    ]
    names = [m.location.name for m in parse_geocoding({"results": results})]
    assert names == ["Fine"]


# --------------------------------------------------------------------------- forecast request


def test_forecast_request_is_a_single_call_with_canonical_units(api: FakeApi) -> None:
    OpenMeteoForecaster(api.client()).fetch(yerevan_location(), NOW)
    assert api.count(FORECAST_HOST) == 1
    params = api.requests[0].url.params
    assert params["timeformat"] == "unixtime"
    assert params["timezone"] == "auto"
    assert params["temperature_unit"] == "celsius"
    assert params["wind_speed_unit"] == "kmh"
    assert params["precipitation_unit"] == "mm"
    assert params["current"] == ",".join(CURRENT_VARS)
    assert "pressure_msl" in params["current"]
    assert {"hourly", "daily", "forecast_days"} <= set(params.keys())


def test_provider_error_reason_is_reported(api: FakeApi) -> None:
    api.forecast = lambda r: httpx.Response(
        400, json={"error": True, "reason": "Latitude must be in range of -90 to 90°."}
    )
    with pytest.raises(ProviderError, match="Latitude must be in range"):
        OpenMeteoForecaster(api.client()).fetch(yerevan_location(), NOW)


# --------------------------------------------------------------------------- forecast parsing


def parse(payload: dict[str, Any]):  # type: ignore[no-untyped-def]
    return parse_forecast(payload, yerevan_location(), NOW)


def test_parse_forecast_happy_path() -> None:
    report = parse(forecast_payload())
    assert report.timezone == "Asia/Yerevan"
    assert report.current.temperature_c == 14.6
    assert report.current.relative_humidity_pct == 61
    assert report.current.pressure_msl_hpa == 1018.3
    assert report.current.is_day is True
    assert report.current.time.tzinfo == ZoneInfo("Asia/Yerevan")
    assert report.current.time.hour == 13 and report.current.time.minute == 15
    assert len(report.hourly) == 144
    assert len(report.daily) == 6
    assert report.daily[0].date.isoformat() == "2026-10-30"
    assert report.fetched_at == NOW


def test_hourly_starts_at_local_midnight_in_location_timezone() -> None:
    report = parse(forecast_payload())
    first = report.hourly[0].time
    assert (first.hour, first.minute) == (0, 0)
    assert first.utcoffset() is not None and first.utcoffset().total_seconds() == 4 * 3600


def test_null_optional_fields_become_none() -> None:
    payload = forecast_payload(
        current_overrides={
            "relative_humidity_2m": None,
            "wind_speed_10m": None,
            "pressure_msl": None,
            "apparent_temperature": "warm",
            "weather_code": None,
            "is_day": None,
        }
    )
    current = parse(payload).current
    assert current.relative_humidity_pct is None
    assert current.wind_speed_kmh is None
    assert current.pressure_msl_hpa is None
    assert current.apparent_temperature_c is None
    assert current.weather_code is None
    assert current.is_day is None


def test_out_of_range_values_are_rejected_not_invented() -> None:
    payload = forecast_payload(
        current_overrides={"relative_humidity_2m": 140, "cloud_cover": -5, "wind_speed_10m": -1}
    )
    current = parse(payload).current
    assert current.relative_humidity_pct is None
    assert current.cloud_cover_pct is None
    assert current.wind_speed_kmh is None


def test_missing_current_temperature_is_malformed() -> None:
    payload = forecast_payload(current_overrides={"temperature_2m": None})
    with pytest.raises(ProviderError, match="unexpected format"):
        parse(payload)


def test_optional_hourly_series_with_wrong_length_is_ignored() -> None:
    payload = forecast_payload()
    payload["hourly"]["precipitation_probability"] = [5, 6, 7]
    report = parse(payload)
    assert all(h.precipitation_probability_pct is None for h in report.hourly)


def test_required_hourly_series_with_wrong_length_is_malformed() -> None:
    payload = forecast_payload()
    payload["hourly"]["temperature_2m"] = payload["hourly"]["temperature_2m"][:-1]
    with pytest.raises(ProviderError):
        parse(payload)


def test_hourly_points_without_temperature_are_dropped() -> None:
    payload = forecast_payload()
    payload["hourly"]["temperature_2m"][5] = None
    assert len(parse(payload).hourly) == 143


def test_daily_missing_rain_values_are_none() -> None:
    payload = forecast_payload()
    payload["daily"]["precipitation_sum"] = [None] * 6
    del payload["daily"]["precipitation_probability_max"]
    day = parse(payload).daily[0]
    assert day.precipitation_sum_mm is None
    assert day.precipitation_probability_max_pct is None


def test_unknown_weather_code_is_preserved() -> None:
    payload = forecast_payload(current_overrides={"weather_code": 42})
    assert parse(payload).current.weather_code == 42


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("current"),
        lambda p: p.pop("timezone"),
        lambda p: p["hourly"].__setitem__("time", ["2026-10-30T00:00"] * 3),
        lambda p: p["current"].__setitem__("time", "2026-10-30T13:15"),
        lambda p: p["daily"].pop("temperature_2m_max"),
    ],
)
def test_structural_problems_are_malformed(mutation: Any) -> None:
    payload = forecast_payload()
    mutation(payload)
    with pytest.raises(ProviderError, match="unexpected format"):
        parse(payload)


def test_non_object_payload_is_malformed() -> None:
    with pytest.raises(ProviderError):
        parse_forecast([1, 2, 3], yerevan_location(), NOW)


def test_mismatched_units_are_rejected() -> None:
    payload = forecast_payload()
    payload["current_units"]["temperature_2m"] = "°F"
    with pytest.raises(ProviderError):
        parse(payload)


def test_unknown_timezone_falls_back_to_fixed_offset() -> None:
    payload = forecast_payload()
    payload["timezone"] = "Mars/Olympus_Mons"
    report = parse(payload)
    offset = report.current.time.utcoffset()
    assert offset is not None and offset.total_seconds() == 4 * 3600


def test_location_without_timezone_gets_the_forecast_timezone() -> None:
    from dataclasses import replace

    location = replace(yerevan_location(), timezone=None)
    report = parse_forecast(forecast_payload(), location, NOW)
    assert report.location.timezone == "Asia/Yerevan"
