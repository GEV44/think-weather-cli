"""Postal-code handling: country required, verified fallback only, nothing invented."""

from __future__ import annotations

import httpx
import pytest

from conftest import BEVERLY_HILLS, GEOCODE_HOST, ZIP_HOST, FakeApi, geo_result, postal_result
from weather_cli.errors import (
    AmbiguousLocationError,
    ExitCode,
    InputError,
    LocationNotFoundError,
    ProviderError,
)
from weather_cli.locations import parse_query
from weather_cli.providers.open_meteo import OpenMeteoForecaster, OpenMeteoGeocoder
from weather_cli.providers.postal import (
    SUPPORTED_COUNTRIES,
    ZippopotamPostalLookup,
    district_prefix,
    parse_postal,
)
from weather_cli.service import CacheMode, WeatherService

ZIP_90210 = {
    "post code": "90210",
    "country": "United States",
    "country abbreviation": "US",
    "places": [
        {
            "place name": "Beverly Hills",
            "longitude": "-118.4065",
            "latitude": "34.0901",
            "state": "California",
            "state abbreviation": "CA",
        }
    ],
}


def service(api: FakeApi) -> WeatherService:
    http = api.client()
    return WeatherService(
        OpenMeteoGeocoder(http),
        OpenMeteoForecaster(http),
        postal=ZippopotamPostalLookup(http),
        cache_mode=CacheMode.OFF,
    )


def test_us_zip_resolves_via_open_meteo(api: FakeApi) -> None:
    api.geocode = {"results": [BEVERLY_HILLS]}
    resolution = service(api).resolve(parse_query("90210", "US"))
    assert resolution.location is not None
    assert resolution.location.name == "Beverly Hills"
    assert resolution.location.postcode == "90210"
    assert api.count(ZIP_HOST) == 0


def test_leading_zero_zip_is_preserved_end_to_end(api: FakeApi) -> None:
    holtsville = geo_result(
        "Holtsville", 40.8, -73.0, country="United States", cc="US", admin1="New York",
        tz="America/New_York", postcodes=["00501", "00544"],
    )  # fmt: skip
    api.geocode = {"results": [holtsville]}
    resolution = service(api).resolve(parse_query("00501", "US"))
    assert api.requests[0].url.params["name"] == "00501"
    assert resolution.location is not None and resolution.location.postcode == "00501"


def test_falls_back_to_zippopotam_for_supported_country(api: FakeApi) -> None:
    api.geocode = {"generationtime_ms": 0.1}
    api.postal = ZIP_90210
    resolution = service(api).resolve(parse_query("90210", "US"))
    assert api.count(GEOCODE_HOST) == 1 and api.count(ZIP_HOST) == 1
    assert api.requests[1].url.path == "/us/90210"
    location = resolution.location
    assert location is not None
    assert location.resolved_by == "zippopotam.us"
    assert (location.latitude, location.longitude) == (34.0901, -118.4065)


def test_armenian_postcode_is_never_invented(api: FakeApi) -> None:
    """AM is not in Zippopotam's list: no fallback request and no fabricated coordinates."""
    api.geocode = {"generationtime_ms": 0.1}
    with pytest.raises(LocationNotFoundError) as info:
        service(api).resolve(parse_query("0010", "AM"))
    assert api.count(ZIP_HOST) == 0
    assert "0010" in info.value.message
    assert info.value.hint is not None and "weather Yerevan --country AM" in info.value.hint
    assert info.value.exit_code is ExitCode.NOT_FOUND
    assert "AM" not in SUPPORTED_COUNTRIES


def test_postcode_without_country_is_refused_before_any_request(api: FakeApi) -> None:
    with pytest.raises(InputError, match="needs a country"):
        service(api).resolve(parse_query("90210"))
    assert api.requests == []


def test_unknown_zip_in_supported_country(api: FakeApi) -> None:
    api.geocode = {}
    api.postal = lambda r: httpx.Response(404, json={})
    with pytest.raises(LocationNotFoundError, match="99999"):
        service(api).resolve(parse_query("99999", "US"))


def test_parse_postal_handles_garbage() -> None:
    assert parse_postal(None, "1") == []
    assert parse_postal({"places": "x"}, "1") == []
    bad_place = {"places": [{"place name": "X", "latitude": "north", "longitude": "1"}, 5]}
    assert parse_postal(bad_place, "1") == []


def test_supports_is_case_insensitive() -> None:
    lookup = ZippopotamPostalLookup(FakeApi().client())
    assert lookup.supports("us") and lookup.supports("DE")
    assert not lookup.supports("AM")
    assert lookup.lookup("AM", "0010") == []


@pytest.mark.parametrize(
    ("country", "postcode", "expected"),
    [
        ("GB", "SW1A 1AA", "SW1A"),
        ("gb", "sw1a1aa", "SW1A"),
        ("GB", "  sw1a   1aa  ", "SW1A"),
        ("GB", "EC1A 1BB", "EC1A"),
        ("GB", "M1 1AE", "M1"),
        ("GB", "B33 8TH", "B33"),
        ("GB", "SW1A", None),
        ("GB", "SW1A 1A", None),
        ("GB", "SW1A 1AA XX", None),
        ("GB", "ABC1 1AA", None),
        ("GB", "12345678", None),
        ("CA", "A1A 1A1", "A1A"),
        ("CA", "A1A1A1", "A1A"),
        ("CA", "M5V 3L9", "M5V"),
        ("ca", "m5v3l9", "M5V"),
        ("CA", "M5V", None),
        ("CA", "M5V 3LL", None),
        ("NL", "1234 AB", "1234"),
        ("NL", "1234AB", "1234"),
        ("NL", "1012 AB", "1012"),
        ("nl", "1012ab", "1012"),
        ("NL", "1012", None),
        ("NL", "1012 A1", None),
        ("US", "90210", None),
        ("AM", "0010", None),
    ],
)
def test_district_prefix(country: str, postcode: str, expected: str | None) -> None:
    assert district_prefix(country, postcode) == expected


@pytest.mark.parametrize(
    ("country", "postcode", "prefix"),
    [("GB", "SW1A 1AA", "SW1A"), ("CA", "M5V 3L9", "M5V"), ("NL", "1012 AB", "1012")],
)
def test_lookup_exact_miss_then_district(
    api: FakeApi, country: str, postcode: str, prefix: str
) -> None:
    def postal(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/{country.lower()}/{prefix}":
            return httpx.Response(200, json=postal_result("District", 51.5, -0.1, country))
        return httpx.Response(404)

    api.postal = postal
    matches = ZippopotamPostalLookup(api.client()).lookup(country, postcode)
    assert len(matches) == 1
    assert matches[0].location.postcode == prefix
    assert matches[0].postcodes == (prefix,)
    assert [r.url.path for r in api.requests] == [
        f"/{country.lower()}/{postcode}",
        f"/{country.lower()}/{prefix}",
    ]


def test_lookup_exact_hit_does_not_request_district(api: FakeApi) -> None:
    api.postal = postal_result("Exact", 51.5, -0.1, "GB")
    matches = ZippopotamPostalLookup(api.client()).lookup("GB", "SW1A 1AA")
    assert matches[0].location.postcode == "SW1A 1AA"
    assert matches[0].postcodes == ("SW1A 1AA",)
    assert api.count(ZIP_HOST) == 1


@pytest.mark.parametrize(
    ("country", "postcode", "requests"), [("DE", "10115", 1), ("GB", "SW1A 1AA", 2)]
)
def test_lookup_misses(api: FakeApi, country: str, postcode: str, requests: int) -> None:
    api.postal = lambda r: httpx.Response(404)
    assert ZippopotamPostalLookup(api.client()).lookup(country, postcode) == []
    assert api.count(ZIP_HOST) == requests


def test_lookup_exact_error_does_not_request_district(api: FakeApi) -> None:
    api.postal = lambda r: httpx.Response(400)
    with pytest.raises(ProviderError):
        ZippopotamPostalLookup(api.client()).lookup("GB", "SW1A 1AA")
    assert api.count(ZIP_HOST) == 1


@pytest.mark.parametrize("exact_geocoded", [False, True])
def test_full_postcode_exact_hit_has_no_district_note(api: FakeApi, exact_geocoded: bool) -> None:
    api.geocode = {
        "results": [geo_result("Exact", 51.5, -0.1, cc="GB", postcodes=["SW1A 1AA"])]
        if exact_geocoded
        else []
    }
    api.postal = postal_result("Exact", 51.5, -0.1, "GB")
    resolution = service(api).resolve(parse_query("SW1A 1AA", "GB"))
    assert resolution.location is not None and resolution.location.postcode == "SW1A 1AA"
    assert resolution.note is None
    assert api.count(GEOCODE_HOST) == 1
    assert api.count(ZIP_HOST) == (0 if exact_geocoded else 1)


def test_district_ambiguity_requires_choice_and_keeps_note(api: FakeApi) -> None:
    api.geocode = {}
    payload = postal_result("District", 51.5, -0.1, "GB")
    payload["places"].append(postal_result("Another place", 51.7, -0.3, "GB")["places"][0])
    api.postal = lambda r: (
        httpx.Response(200, json=payload) if r.url.path == "/gb/SW1A" else httpx.Response(404)
    )
    for postcode in ["SW1A", "SW1A 1AA"]:
        with pytest.raises(AmbiguousLocationError):
            service(api).resolve(parse_query(postcode, "GB"))
    chosen = service(api).resolve(parse_query("SW1A 1AA", "GB"), lambda candidates: candidates[1])
    assert chosen.location is not None and chosen.location.name == "Another place"
    assert chosen.location.postcode == "SW1A 1AA"
    assert chosen.note is not None and "only lists the district SW1A" in chosen.note
