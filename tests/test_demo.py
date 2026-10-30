"""Offline demo: no network, same code path, clearly labelled, packaged fixture."""

from __future__ import annotations

import io
import json
from importlib.resources import files

import pytest

from weather_cli.cli import main
from weather_cli.demo import DemoForecaster, DemoGeocoder, build_demo_service, load_fixture
from weather_cli.errors import LocationNotFoundError
from weather_cli.locations import parse_query
from weather_cli.models import Origin


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return main(argv, stdin=io.StringIO(), stdout=out, stderr=err), out.getvalue(), err.getvalue()


def test_fixture_is_package_data() -> None:
    resource = files("weather_cli.demo").joinpath("fixtures/yerevan_sample.json")
    assert resource.is_file()
    data = load_fixture()
    assert "SYNTHETIC" in data["_provenance"]


def test_demo_service_uses_real_parser_and_marks_origin() -> None:
    service = build_demo_service()
    resolution = service.resolve(parse_query("Yerevan"))
    assert resolution.location is not None
    result = service.fetch(resolution.location)
    assert result.origin is Origin.DEMO
    assert result.as_of == result.report.current.time  # anchored to the sample, not "now"
    assert result.report.fetched_at == result.report.current.time
    assert result.report.current.relative_humidity_pct == 52


@pytest.mark.parametrize("name", ["yerevan", "YEREVAN", "Երևան"])
def test_demo_accepts_yerevan_spellings(name: str) -> None:
    assert DemoGeocoder().search(name, None)


@pytest.mark.parametrize(("name", "country"), [("London", None), ("Yerevan", "US")])
def test_demo_rejects_other_places(name: str, country: str | None) -> None:
    with pytest.raises(LocationNotFoundError, match="only contains"):
        DemoGeocoder().search(name, country)


def test_demo_output_is_labelled_everywhere() -> None:
    code, out, _ = run(["Yerevan", "--demo", "--forecast", "--hours", "12"])
    assert code == 0
    assert "DEMO / SAMPLE DATA" in out and "NOT live weather" in out
    code, out, _ = run(["Yerevan", "--demo", "--plain"])
    assert "[DEMO / SAMPLE DATA]" in out and out.isascii()


def test_demo_json_and_unit_switch() -> None:
    code, out, _ = run(["--demo", "--output", "json", "--units", "f", "--forecast"])
    data = json.loads(out)
    assert code == 0
    assert data["mode"] == "offline-synthetic-sample"
    assert data["location"]["name"] == "Yerevan"
    assert data["current"]["temperature"] == 66.2  # 19.0 °C sample
    assert len(data["daily"]) == 5
    assert data["daily"][0]["date"] == "2026-10-01"


def test_demo_hours_start_at_sample_time() -> None:
    _code, out, _ = run(["Yerevan", "--demo", "--output", "json", "--hours", "3"])
    hours = json.loads(out)["hourly"]
    assert [h["time"][11:16] for h in hours] == ["12:00", "13:00", "14:00"]


def test_demo_needs_no_cache(isolated_env: object) -> None:
    from pathlib import Path

    run(["Yerevan", "--demo"])
    assert not Path(str(isolated_env)).exists()


def test_demo_forecaster_namespace_is_isolated() -> None:
    assert DemoForecaster.cache_namespace == "demo"
