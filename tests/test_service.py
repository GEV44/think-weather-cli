"""Service: location resolution and read-through caching across instances."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from conftest import (
    FORECAST_HOST,
    GEOCODE_HOST,
    SPRINGFIELDS,
    YEREVAN,
    ZIP_HOST,
    Clock,
    FakeApi,
    geo_result,
    postal_result,
    yerevan_location,
)
from weather_cli.cache import SQLiteCache
from weather_cli.errors import AmbiguousLocationError, LocationNotFoundError, ProviderError
from weather_cli.locations import parse_query
from weather_cli.models import Location, Origin
from weather_cli.providers.open_meteo import OpenMeteoForecaster, OpenMeteoGeocoder
from weather_cli.providers.postal import ZippopotamPostalLookup
from weather_cli.service import CacheMode, WeatherService

START = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def make_service(
    api: FakeApi, cache_path: Path | None, clock: Clock, mode: CacheMode = CacheMode.NORMAL
) -> WeatherService:
    """A fresh service + client each time, like a new process sharing the cache file."""
    http = api.client()
    return WeatherService(
        OpenMeteoGeocoder(http),
        OpenMeteoForecaster(http),
        cache=SQLiteCache(cache_path) if cache_path else None,
        cache_mode=mode,
        clock=clock,
    )


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.sqlite3"


# --------------------------------------------------------------------------- resolution


def test_ambiguous_city_without_chooser_raises_with_candidates(api: FakeApi, clock: Clock) -> None:
    api.geocode = {"results": SPRINGFIELDS}
    with pytest.raises(AmbiguousLocationError) as info:
        make_service(api, None, clock).resolve(parse_query("Springfield", "US"))
    assert len(info.value.candidates) == 2
    assert info.value.hint is not None and "Springfield, Missouri" in info.value.hint


def test_ambiguous_city_with_chooser_uses_the_choice(api: FakeApi, clock: Clock) -> None:
    api.geocode = {"results": SPRINGFIELDS}
    offered: list[Sequence[Location]] = []

    def choose(candidates: Sequence[Location]) -> Location:
        offered.append(candidates)
        return candidates[1]

    resolution = make_service(api, None, clock).resolve(parse_query("Springfield"), choose)
    assert resolution.location is not None and resolution.location.admin1 == "Illinois"
    assert len(offered[0]) == 2


def test_unknown_city_is_not_found(api: FakeApi, clock: Clock) -> None:
    api.geocode = {"generationtime_ms": 0.2}
    with pytest.raises(LocationNotFoundError, match="DefinitelyNotARealCity999999"):
        make_service(api, None, clock).resolve(parse_query("DefinitelyNotARealCity999999", "US"))


def test_geocoder_outage_propagates_as_provider_error(api: FakeApi, clock: Clock) -> None:
    api.geocode = lambda r: httpx.Response(503)
    with pytest.raises(ProviderError):
        make_service(api, None, clock).resolve(parse_query("Yerevan"))


def test_location_lookups_are_cached(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    make_service(api, cache_path, clock).resolve(parse_query("Yerevan", "AM"))
    make_service(api, cache_path, clock).resolve(parse_query("yerevan", "am"))
    assert api.count(GEOCODE_HOST) == 1
    clock.advance(hours=25)
    make_service(api, cache_path, clock).resolve(parse_query("Yerevan", "AM"))
    assert api.count(GEOCODE_HOST) == 2


def test_empty_geocoding_results_are_not_cached(
    api: FakeApi, clock: Clock, cache_path: Path
) -> None:
    api.geocode = {}
    for _ in range(2):
        with pytest.raises(LocationNotFoundError):
            make_service(api, cache_path, clock).resolve(parse_query("Atlantis"))
    assert api.count(GEOCODE_HOST) == 2


# --------------------------------------------------------------------------- weather cache


def test_second_process_within_ttl_uses_cache(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    first = make_service(api, cache_path, clock).fetch(yerevan_location())
    clock.advance(minutes=5)
    second = make_service(api, cache_path, clock).fetch(yerevan_location())
    assert api.count(FORECAST_HOST) == 1
    assert first.origin is Origin.LIVE and second.origin is Origin.CACHE
    assert second.report.current == first.report.current
    assert second.as_of == clock.now


def test_expired_entry_is_refetched(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    make_service(api, cache_path, clock).fetch(yerevan_location())
    clock.advance(minutes=10, seconds=1)
    result = make_service(api, cache_path, clock).fetch(yerevan_location())
    assert api.count(FORECAST_HOST) == 2
    assert result.origin is Origin.LIVE


def test_refresh_skips_reading_but_still_writes(
    api: FakeApi, clock: Clock, cache_path: Path
) -> None:
    make_service(api, cache_path, clock).fetch(yerevan_location())
    refreshed = make_service(api, cache_path, clock, CacheMode.REFRESH).fetch(yerevan_location())
    assert refreshed.origin is Origin.LIVE
    assert api.count(FORECAST_HOST) == 2
    make_service(api, cache_path, clock).fetch(yerevan_location())
    assert api.count(FORECAST_HOST) == 2  # the refreshed copy was cached


def test_no_cache_neither_reads_nor_writes(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    make_service(api, cache_path, clock, CacheMode.OFF).fetch(yerevan_location())
    make_service(api, cache_path, clock, CacheMode.OFF).fetch(yerevan_location())
    assert api.count(FORECAST_HOST) == 2
    assert not cache_path.exists()


def test_per_call_refresh(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    service = make_service(api, cache_path, clock)
    service.fetch(yerevan_location())
    service.fetch(yerevan_location(), refresh=True)
    assert api.count(FORECAST_HOST) == 2


def test_nearby_but_distinct_coordinates_do_not_share_cache(
    api: FakeApi, clock: Clock, cache_path: Path
) -> None:
    from dataclasses import replace

    service = make_service(api, cache_path, clock)
    service.fetch(yerevan_location())
    service.fetch(replace(yerevan_location(), latitude=40.1790))  # ~150 m north
    assert api.count(FORECAST_HOST) == 2


def test_corrupt_cached_report_falls_back_to_live(
    api: FakeApi, clock: Clock, cache_path: Path
) -> None:
    import sqlite3
    from contextlib import closing

    make_service(api, cache_path, clock).fetch(yerevan_location())
    with closing(sqlite3.connect(cache_path)) as db, db:
        db.execute("UPDATE entries SET payload = '{not json'")
    result = make_service(api, cache_path, clock).fetch(yerevan_location())
    assert result.origin is Origin.LIVE
    assert api.count(FORECAST_HOST) == 2


def test_cached_report_shows_the_requested_place_name(
    api: FakeApi, clock: Clock, cache_path: Path
) -> None:
    from dataclasses import replace

    make_service(api, cache_path, clock).fetch(yerevan_location())
    alias = replace(yerevan_location(), name="Yerevan (alias)", postcode="0010")
    result = make_service(api, cache_path, clock).fetch(alias)
    assert result.report.location.name == "Yerevan (alias)"


# --------------------------------------------------------------------------- stale-if-error


def test_stale_report_is_served_during_outage(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    make_service(api, cache_path, clock).fetch(yerevan_location())
    clock.advance(minutes=30)
    api.forecast = lambda r: httpx.Response(503)
    result = make_service(api, cache_path, clock).fetch(yerevan_location())
    assert result.origin is Origin.STALE
    assert result.as_of - result.report.fetched_at == clock.now - START


def test_stale_window_is_bounded(api: FakeApi, clock: Clock, cache_path: Path) -> None:
    make_service(api, cache_path, clock).fetch(yerevan_location())
    clock.advance(minutes=10 + 61)
    api.forecast = lambda r: httpx.Response(503)
    with pytest.raises(ProviderError):
        make_service(api, cache_path, clock).fetch(yerevan_location())


@pytest.mark.parametrize("status", [429, 400])
def test_no_stale_fallback_for_rate_limits_or_bad_requests(
    api: FakeApi, clock: Clock, cache_path: Path, status: int
) -> None:
    make_service(api, cache_path, clock).fetch(yerevan_location())
    clock.advance(minutes=20)
    api.forecast = lambda r: httpx.Response(status, json={"reason": "no"})
    with pytest.raises(ProviderError):
        make_service(api, cache_path, clock).fetch(yerevan_location())


def test_outage_without_cache_raises(api: FakeApi, clock: Clock) -> None:
    api.forecast = lambda r: httpx.Response(502)
    with pytest.raises(ProviderError, match="temporarily unavailable"):
        make_service(api, None, clock).fetch(yerevan_location())


def test_geocode_payload_fixture_is_valid(api: FakeApi, clock: Clock) -> None:
    api.geocode = {"results": [YEREVAN]}
    resolution = make_service(api, None, clock).resolve(parse_query("Yerevan"))
    assert resolution.location is not None and resolution.location.country == "Armenia"


@pytest.mark.parametrize(
    ("country", "postcode", "prefix"),
    [("GB", "SW1A 1AA", "SW1A"), ("CA", "M5V 3L9", "M5V"), ("NL", "1012 AB", "1012")],
)
@pytest.mark.parametrize("district_geocoded", [False, True])
def test_district_resolution_and_cache_preserve_note(
    api: FakeApi,
    clock: Clock,
    cache_path: Path,
    country: str,
    postcode: str,
    prefix: str,
    district_geocoded: bool,
) -> None:
    def geocode(request: httpx.Request) -> httpx.Response:
        results = []
        if district_geocoded and request.url.params["name"] == prefix:
            results = [geo_result("Geocoded district", 51.6, -0.2, cc=country, postcodes=[prefix])]
        return httpx.Response(200, json={"results": results})

    payload = postal_result("District", 51.5, -0.1, country)
    if district_geocoded:
        payload["places"].append(postal_result("Another place", 51.7, -0.3, country)["places"][0])

    def postal(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/{country.lower()}/{prefix}":
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    api.geocode, api.postal = geocode, postal

    def service() -> WeatherService:
        http = api.client()
        return WeatherService(
            OpenMeteoGeocoder(http),
            OpenMeteoForecaster(http),
            postal=ZippopotamPostalLookup(http),
            cache=SQLiteCache(cache_path),
            clock=clock,
        )

    full_query = parse_query(postcode, country)
    first = service().resolve(full_query)
    assert first.location is not None
    assert first.location.postcode == postcode
    assert not first.exact
    assert first.note == (
        f"Zippopotam.us only lists the district {prefix} for '{postcode}'; "
        "showing weather for that district."
    )
    assert api.count(ZIP_HOST) == 2
    cached = service().resolve(full_query)
    assert cached == first
    assert api.count(ZIP_HOST) == 2

    direct = service().resolve(parse_query(prefix, country))
    assert direct.location is not None
    assert (first.location.name, first.location.latitude, first.location.longitude) == (
        direct.location.name,
        direct.location.latitude,
        direct.location.longitude,
    )
    assert direct.note is None
