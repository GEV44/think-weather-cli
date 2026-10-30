"""Regression tests for defects found in an independent code review."""

from __future__ import annotations

import io
import json

import httpx
import pytest
from rich.console import Console

from conftest import SPRINGFIELDS, ZIP_HOST, FakeApi, forecast_payload, geo_result
from weather_cli.cli import _prepare_stream, main
from weather_cli.errors import ExitCode
from weather_cli.interactive import InteractiveSession
from weather_cli.locations import parse_query
from weather_cli.models import UnitSystem
from weather_cli.providers.open_meteo import OpenMeteoForecaster, OpenMeteoGeocoder
from weather_cli.providers.postal import ZippopotamPostalLookup
from weather_cli.rendering import THEME, Formatter, ViewOptions
from weather_cli.service import CacheMode, WeatherService

VERMONT = geo_result(
    "Springfield", 43.3, -72.5, country="United States", cc="US", admin1="Vermont",
    tz="America/New_York", population=9_000, ident=13,
)  # fmt: skip


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return main(argv, stdin=io.StringIO(), stdout=out, stderr=err), out.getvalue(), err.getvalue()


def test_city_region_query_searches_deep_enough(patch_http: FakeApi) -> None:
    """A small namesake outside the top 10 must still be reachable via "City, Region"."""

    def geocode(request: httpx.Request) -> httpx.Response:
        count = int(request.url.params["count"])
        results = [*SPRINGFIELDS] * 5  # ten bigger Springfields first
        return httpx.Response(200, json={"results": ([*results, VERMONT])[:count]})

    patch_http.geocode = geocode
    code, out, err = run(["Springfield, Vermont", "--country", "US", "--plain"])
    assert code == 0, err
    assert "Springfield, Vermont" in out
    assert patch_http.requests[0].url.params["count"] == "100"


def test_plain_city_query_keeps_small_request(patch_http: FakeApi) -> None:
    run(["Yerevan", "--plain"])
    assert patch_http.requests[0].url.params["count"] == "10"


@pytest.mark.parametrize("bad", [10**15, -5, 0])
def test_out_of_range_timestamps_are_malformed_not_silent(patch_http: FakeApi, bad: int) -> None:
    payload = forecast_payload()
    payload["hourly"]["time"][3] = bad
    patch_http.forecast = payload
    code, out, err = run(["Yerevan"])
    assert code == ExitCode.PROVIDER
    assert out == "" and "unexpected format" in err


def test_out_of_range_current_time_is_malformed(patch_http: FakeApi) -> None:
    patch_http.forecast = forecast_payload(current_overrides={"time": 10**15})
    assert run(["Yerevan"])[0] == ExitCode.PROVIDER


@pytest.mark.parametrize("header", ["inf", "1e999", "nan"])
def test_non_finite_retry_after_is_handled(patch_http: FakeApi, header: str) -> None:
    patch_http.geocode = lambda r: httpx.Response(429, headers={"Retry-After": header})
    code, _, err = run(["Yerevan"])
    assert code == ExitCode.RATE_LIMITED
    assert "internal error" not in err


def test_non_pipe_os_errors_are_internal_errors_not_silent_success(
    patch_http: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weather_cli.cli as cli

    def disk_full(*args: object, **kwargs: object) -> str:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli, "render_plain", disk_full)
    code, _, err = run(["Yerevan", "--plain"])
    assert code == ExitCode.INTERNAL
    assert "internal error" in err


def test_markup_like_place_names_are_printed_literally(api: FakeApi) -> None:
    tricky = [
        geo_result("Foo [/bar]", 1, 1, population=10, ident=1),
        geo_result("Foo [/bar]", 30, 30, population=12, ident=2),
    ]
    api.geocode = {"results": tricky}
    http = api.client()
    service = WeatherService(
        OpenMeteoGeocoder(http), OpenMeteoForecaster(http), cache_mode=CacheMode.OFF
    )
    out = io.StringIO()
    answers = iter(["", "Foo [/bar]", "1", "q"])
    session = InteractiveSession(
        service, Console(file=out, width=100, theme=THEME), lambda _: next(answers),
        lambda error: None, ViewOptions(),
    )  # fmt: skip
    assert session.run() == 0
    assert "1. Foo [/bar]" in out.getvalue()


def test_tiny_negative_temperatures_are_not_negative_zero() -> None:
    fmt = Formatter(UnitSystem.METRIC, ascii_only=True)
    assert fmt.temp(-0.04) == "0.0 C"
    assert json.dumps(fmt.temp_value(-0.04)) == "0.0"


def test_postcode_without_exact_geocoder_hit_tries_postal_lookup(api: FakeApi) -> None:
    unrelated = geo_result("Elsewhere", 1, 1, cc="US", postcodes=["11111"])
    api.geocode = {"results": [unrelated]}
    api.postal = {
        "country abbreviation": "US",
        "country": "United States",
        "places": [{"place name": "Right Place", "latitude": "34.1", "longitude": "-118.4"}],
    }
    http = api.client()
    service = WeatherService(
        OpenMeteoGeocoder(http), OpenMeteoForecaster(http),
        postal=ZippopotamPostalLookup(http), cache_mode=CacheMode.OFF,
    )  # fmt: skip
    resolution = service.resolve(parse_query("90210", "US"))
    assert resolution.location is not None and resolution.location.name == "Right Place"
    assert api.count(ZIP_HOST) == 1


def test_postal_outage_keeps_geocoder_closest_match(api: FakeApi) -> None:
    unrelated = geo_result("Elsewhere", 1, 1, cc="US", postcodes=[])
    api.geocode = {"results": [unrelated]}
    api.postal = lambda r: httpx.Response(503)
    http = api.client()
    service = WeatherService(
        OpenMeteoGeocoder(http), OpenMeteoForecaster(http),
        postal=ZippopotamPostalLookup(http), cache_mode=CacheMode.OFF,
    )  # fmt: skip
    resolution = service.resolve(parse_query("90210", "US"))
    assert resolution.location is not None and resolution.location.name == "Elsewhere"
    assert resolution.note is not None and "Closest match" in resolution.note


class _TtyWrapper(io.TextIOWrapper):
    def isatty(self) -> bool:
        return True


def test_redirected_legacy_code_page_output_keeps_place_names() -> None:
    # Windows redirects stdout with the legacy code page; "Երևան" used to become "?????".
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1251")
    _prepare_stream(stream)
    stream.write("Երևան, Île-de-France")
    stream.flush()
    assert raw.getvalue().decode("utf-8") == "Երևան, Île-de-France"


def test_terminal_keeps_its_encoding_and_never_crashes_on_a_missing_glyph() -> None:
    raw = io.BytesIO()
    stream = _TtyWrapper(raw, encoding="cp1251")
    _prepare_stream(stream)
    stream.write("Երևան")
    stream.flush()
    assert stream.encoding == "cp1251"
    assert raw.getvalue() == b"?????"


def test_streams_without_reconfigure_are_left_alone() -> None:
    stream = io.StringIO()
    _prepare_stream(stream)
    stream.write("Երևան")
    assert stream.getvalue() == "Երևան"
