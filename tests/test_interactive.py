"""Interactive console driven by scripted answers."""

from __future__ import annotations

import io
from collections.abc import Iterable

import httpx
from rich.console import Console

from conftest import BEVERLY_HILLS, FORECAST_HOST, SPRINGFIELDS, YEREVAN, FakeApi
from weather_cli.errors import WeatherError
from weather_cli.interactive import InteractiveSession
from weather_cli.models import UnitSystem
from weather_cli.providers.open_meteo import OpenMeteoForecaster, OpenMeteoGeocoder
from weather_cli.providers.postal import ZippopotamPostalLookup
from weather_cli.rendering import THEME, ViewOptions
from weather_cli.service import CacheMode, WeatherService


class Script:
    def __init__(self, answers: Iterable[str]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def session(
    api: FakeApi, answers: Iterable[str]
) -> tuple[InteractiveSession, io.StringIO, list[WeatherError], Script]:
    http = api.client()
    service = WeatherService(
        OpenMeteoGeocoder(http),
        OpenMeteoForecaster(http),
        postal=ZippopotamPostalLookup(http),
        cache_mode=CacheMode.OFF,
    )
    out = io.StringIO()
    console = Console(file=out, width=100, theme=THEME, emoji=False)
    errors: list[WeatherError] = []
    script = Script(answers)
    return (
        InteractiveSession(service, console, script, errors.append, ViewOptions()),
        out,
        errors,
        script,
    )


def test_search_then_toggle_units_without_network(api: FakeApi) -> None:
    s, out, errors, _ = session(api, ["c", "Yerevan", "u", "u", "q"])
    assert s.run() == 0
    text = out.getvalue()
    assert "14.6°C" in text and "58.3°F" in text
    assert api.count(FORECAST_HOST) == 1  # unit toggles re-render cached data
    assert not errors
    assert "Goodbye" in text


def test_fahrenheit_choice_at_start(api: FakeApi) -> None:
    s, out, _, _ = session(api, ["F", "Yerevan", "q"])
    s.run()
    assert s.view.units is UnitSystem.IMPERIAL
    assert "58.3°F" in out.getvalue()


def test_invalid_unit_answer_is_reasked(api: FakeApi) -> None:
    s, out, _, script = session(api, ["kelvin", "c", "q"])
    s.run()
    assert "Please type C or F" in out.getvalue()
    assert sum("Units" in p for p in script.prompts) == 2


def test_forecast_toggle_and_refresh(api: FakeApi) -> None:
    s, out, _, _ = session(api, ["", "Yerevan", "f", "r", "x", "q"])
    s.run()
    text = out.getvalue()
    assert "5-day forecast" in text
    assert "Unknown option" in text
    assert api.count(FORECAST_HOST) == 2  # initial fetch + explicit refresh


def test_ambiguous_city_numbered_choice(api: FakeApi) -> None:
    api.geocode = {"results": SPRINGFIELDS}
    s, out, errors, _ = session(api, ["", "Springfield", "9", "2", "q"])
    s.run()
    text = out.getvalue()
    assert "Several places match" in text
    assert "Please enter one of the numbers" in text
    assert "Springfield, Illinois" in text
    assert not errors


def test_cancelling_a_choice_returns_to_search(api: FakeApi) -> None:
    api.geocode = {"results": SPRINGFIELDS}
    s, _, _, script = session(api, ["", "Springfield", "", "q"])
    s.run()
    assert api.count(FORECAST_HOST) == 0
    assert script.prompts[-1].startswith("City or postal code")


def test_postcode_prompts_for_country(api: FakeApi) -> None:
    api.geocode = {"results": [BEVERLY_HILLS]}
    s, out, _, script = session(api, ["", "90210", "us", "q"])
    s.run()
    assert any("Country code" in p for p in script.prompts)
    assert api.requests[0].url.params["countryCode"] == "US"
    assert "postal code 90210" in out.getvalue()


def test_errors_are_reported_and_session_continues(api: FakeApi) -> None:
    api.geocode = {}
    s, _, errors, _ = session(api, ["", "Atlantis", "", "q"])
    assert s.run() == 0
    assert len(errors) == 1 and "Atlantis" in errors[0].message


def test_refresh_failure_keeps_session_alive(api: FakeApi) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            from conftest import forecast_payload

            return httpx.Response(200, json=forecast_payload())
        return httpx.Response(503)

    api.forecast = flaky
    s, _, errors, _ = session(api, ["", "Yerevan", "r", "q"])
    assert s.run() == 0
    assert len(errors) == 1 and "unavailable" in errors[0].message


def test_new_search_and_end_of_input(api: FakeApi) -> None:
    api.geocode = {"results": [YEREVAN]}
    s, out, _, _ = session(api, ["", "Yerevan", "n"])  # then EOF
    assert s.run() == 0
    assert "Goodbye" in out.getvalue()


def test_ctrl_c_exits_cleanly(api: FakeApi) -> None:
    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    s, _out, _, _ = session(api, [])
    s.ask = interrupt
    assert s.run() == 0
