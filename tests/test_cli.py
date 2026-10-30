"""End-to-end CLI behaviour through ``main()`` with a fake HTTP transport."""

from __future__ import annotations

import io
import json
import subprocess
import sys

import httpx
import pytest

from conftest import (
    BEVERLY_HILLS,
    FORECAST_HOST,
    GEOCODE_HOST,
    SPRINGFIELDS,
    FakeApi,
    postal_result,
)
from weather_cli.cli import main
from weather_cli.errors import ExitCode


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def run(argv: list[str], *, stdin: io.StringIO | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=stdin or io.StringIO(), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- happy paths


def test_city_text_output(patch_http: FakeApi) -> None:
    code, out, err = run(["Yerevan", "--country", "AM"])
    assert code == 0, err
    assert "Yerevan, Armenia" in out and "14.6°C" in out and "LIVE" in out
    assert err == ""
    assert patch_http.requests[0].url.params["countryCode"] == "AM"


def test_fahrenheit_flag_and_aliases(patch_http: FakeApi) -> None:
    for flag in ("imperial", "fahrenheit", "F"):
        code, out, _ = run(["Yerevan", "--units", flag, "--plain", "--no-cache"])
        assert code == 0
        assert "58.3 F" in out and "mph" in out


def test_us_zip_with_forecast(patch_http: FakeApi) -> None:
    patch_http.geocode = {"results": [BEVERLY_HILLS]}
    code, out, _ = run(["90210", "--country", "US", "--units", "imperial", "--forecast", "--plain"])
    assert code == 0
    assert "Beverly Hills, California, United States - postal code 90210" in out
    assert "5-day forecast" in out


def test_json_output_is_only_json(patch_http: FakeApi) -> None:
    code, out, err = run(["Yerevan", "--output", "json", "--hours", "3", "--verbose"])
    assert code == 0
    data = json.loads(out)
    assert data["mode"] == "live" and len(data["hourly"]) == 3
    assert "[DEBUG]" in err  # diagnostics go to stderr, never stdout


def test_second_run_is_served_from_cache(patch_http: FakeApi) -> None:
    run(["Yerevan", "--plain"])
    code, out, _ = run(["Yerevan", "--plain"])
    assert code == 0
    assert "[CACHED]" in out
    assert patch_http.count(FORECAST_HOST) == 1
    assert patch_http.count(GEOCODE_HOST) == 1


def test_refresh_and_no_cache_flags(patch_http: FakeApi) -> None:
    run(["Yerevan", "--plain"])
    assert "[LIVE]" in run(["Yerevan", "--plain", "--refresh"])[1]
    assert "[LIVE]" in run(["Yerevan", "--plain", "--no-cache"])[1]
    assert patch_http.count(FORECAST_HOST) == 3


def test_refresh_and_no_cache_are_mutually_exclusive() -> None:
    code, _, err = run(["Yerevan", "--refresh", "--no-cache"])
    assert code == ExitCode.USAGE
    assert "not allowed with" in err


def test_no_color_env_disables_ansi(patch_http: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    code, out, _ = run(["Yerevan"])
    assert code == 0 and "\x1b[" not in out


def test_dominant_match_note_is_shown(patch_http: FakeApi) -> None:
    paris = dict(BEVERLY_HILLS, name="Paris", population=2_000_000, latitude=48.8, postcodes=[])
    paris_tx = dict(BEVERLY_HILLS, name="Paris", population=25_000, latitude=33.6, postcodes=[])
    patch_http.geocode = {"results": [paris, paris_tx]}
    code, out, _ = run(["Paris", "--plain"])
    assert code == 0 and "Note: Showing Paris" in out


# --------------------------------------------------------------------------- failures


def test_ambiguous_city_lists_candidates_and_exits_4(patch_http: FakeApi) -> None:
    patch_http.geocode = {"results": SPRINGFIELDS}
    code, out, err = run(["Springfield", "--country", "US"])
    assert code == ExitCode.AMBIGUOUS
    assert out == ""
    assert "matches 2 different places" in err
    assert "Missouri" in err and "Illinois" in err
    assert patch_http.count(FORECAST_HOST) == 0


def test_ambiguity_error_as_json(patch_http: FakeApi) -> None:
    patch_http.geocode = {"results": SPRINGFIELDS}
    code, out, err = run(["Springfield", "--output", "json"])
    assert code == ExitCode.AMBIGUOUS and out == ""
    error = json.loads(err)["error"]
    assert error["code"] == "ambiguous_location"
    assert error["exit_code"] == 4
    assert len(error["candidates"]) == 2


def test_unknown_city_exits_3(patch_http: FakeApi) -> None:
    patch_http.geocode = {"generationtime_ms": 0.1}
    code, _, err = run(["DefinitelyNotARealCity999999", "--country", "US"])
    assert code == ExitCode.NOT_FOUND
    assert "No place called" in err and "hint:" in err
    assert "Traceback" not in err


def test_zip_without_country_exits_2_without_network(patch_http: FakeApi) -> None:
    code, _, err = run(["90210"])
    assert code == ExitCode.USAGE
    assert "--country US" in err
    assert patch_http.requests == []


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["   "], "city name or a postal code"),
        (["Yerevan", "--country", "ARM"], "two-letter"),
        (["Yerevan", "--hours", "0"], "--hours must be between"),
        (["Yerevan", "--hours", "49"], "--hours must be between"),
        (["Yerevan", "--units", "kelvin"], "Unknown units"),
    ],
)
def test_invalid_input_exits_2_before_any_request(
    patch_http: FakeApi, argv: list[str], message: str
) -> None:
    code, _, err = run(argv)
    assert code == ExitCode.USAGE
    assert message in err
    assert patch_http.requests == []


def test_argparse_errors_return_usage_code() -> None:
    assert run(["Yerevan", "--hours", "many"])[0] == ExitCode.USAGE
    assert run(["Yerevan", "--output", "xml"])[0] == ExitCode.USAGE


def test_service_outage_exits_5(patch_http: FakeApi) -> None:
    patch_http.forecast = lambda r: httpx.Response(503)
    code, out, err = run(["Yerevan"])
    assert code == ExitCode.PROVIDER
    assert out == ""
    assert "temporarily unavailable" in err and "hint:" in err


def test_rate_limit_exits_6(patch_http: FakeApi) -> None:
    patch_http.geocode = lambda r: httpx.Response(429, headers={"Retry-After": "600"})
    code, _, err = run(["Yerevan"])
    assert code == ExitCode.RATE_LIMITED
    assert "rate limiting" in err


def test_malformed_provider_data_exits_5(patch_http: FakeApi) -> None:
    patch_http.forecast = {"unexpected": "shape"}
    code, _, err = run(["Yerevan"])
    assert code == ExitCode.PROVIDER and "unexpected format" in err


def test_internal_errors_are_reported_without_traceback(
    patch_http: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    import weather_cli.cli as cli

    def boom(*args: object, **kwargs: object) -> str:
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(cli, "render_plain", boom)
    code, _, err = run(["Yerevan", "--plain"])
    assert code == ExitCode.INTERNAL
    assert "internal error" in err and "Traceback" not in err
    code, _, err = run(["Yerevan", "--plain", "--verbose"])
    assert "Traceback" in err and "ZeroDivisionError" in err


def test_broken_pipe_is_not_an_error(patch_http: FakeApi) -> None:
    class ClosedPipe(io.StringIO):
        def write(self, text: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

    code = main(
        ["Yerevan", "--plain"], stdin=io.StringIO(), stdout=ClosedPipe(), stderr=io.StringIO()
    )
    assert code == ExitCode.OK


# --------------------------------------------------------------------------- modes


def test_no_location_and_not_a_tty_refuses_to_prompt() -> None:
    code, out, err = run([], stdin=io.StringIO("Yerevan\n"))
    assert code == ExitCode.USAGE
    assert "not an interactive terminal" in err
    assert out == ""


def test_help_and_version() -> None:
    code, out, _ = run(["--help"])
    assert code == 0 and "--country" in out and "exit codes" in out
    code, out, _ = run(["--version"])
    assert code == 0 and out.startswith("weather ")


def test_cache_subcommands(patch_http: FakeApi) -> None:
    code, out, _ = run(["cache", "stats"])
    assert code == 0 and "not created yet" in out
    run(["Yerevan", "--plain"])
    code, out, _ = run(["cache", "stats"])
    assert "open-meteo-forecast-v1" in out and "1 fresh" in out
    code, out, _ = run(["cache", "path"])
    assert out.strip().endswith("cache-v1.sqlite3")
    code, out, _ = run(["cache", "clear"])
    assert code == 0 and "Removed 2 cached entries" in out
    assert run(["cache", "bogus"])[0] == ExitCode.USAGE


def test_city_named_cache_is_still_searchable(patch_http: FakeApi) -> None:
    code, _, _ = run(["cache", "--country", "US", "--plain"])
    assert code == 0
    assert patch_http.requests[0].url.params["name"] == "cache"


def test_module_entry_point_runs_demo_in_subprocess() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "weather_cli", "Yerevan", "--demo", "--output", "json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["mode"] == "offline-synthetic-sample"


def test_unknown_city_exit_code_in_subprocess_demo() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "weather_cli", "London", "--demo"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == ExitCode.NOT_FOUND
    assert completed.stdout == ""
    assert "only contains" in completed.stderr


def test_interactive_mode_with_tty(patch_http: FakeApi) -> None:
    answers = iter(["", "Yerevan", "q"])
    out, err = Tty(), io.StringIO()
    code = main([], stdin=Tty(), stdout=out, stderr=err, ask=lambda prompt: next(answers))
    assert code == 0
    assert "Yerevan, Armenia" in out.getvalue()


@pytest.mark.parametrize("output_args", [[], ["--plain"], ["--output", "json"]])
def test_district_note_is_shown(patch_http: FakeApi, output_args: list[str]) -> None:
    patch_http.geocode = {}

    def postal(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gb/SW1A":
            return httpx.Response(200, json=postal_result("Westminster", 51.5, -0.1, "GB"))
        return httpx.Response(404)

    patch_http.postal = postal
    code, out, err = run(["SW1A 1AA", "--country", "GB", "--no-cache", *output_args])
    assert code == 0, err
    note = (
        "Zippopotam.us only lists the district SW1A for 'SW1A 1AA'; "
        "showing weather for that district."
    )
    if "json" in output_args:
        data = json.loads(out)
        assert data["notes"] == [note]
        assert data["location"]["postcode"] == "SW1A 1AA"
    else:
        assert "Note: " + note in " ".join(out.split())
        assert "postal code SW1A 1AA" in out
    assert err == ""
