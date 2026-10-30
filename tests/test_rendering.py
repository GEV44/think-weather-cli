"""Renderers: units shown correctly, honest labels, ASCII/JSON purity, narrow widths."""

from __future__ import annotations

import io
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console

from conftest import forecast_payload, yerevan_location
from weather_cli import charts
from weather_cli.models import FetchResult, Origin, UnitSystem
from weather_cli.providers.open_meteo import parse_forecast
from weather_cli.rendering import (
    THEME,
    Formatter,
    ViewOptions,
    age_text,
    day_label,
    render_json,
    render_plain,
    render_rich,
)
from weather_cli.weather_codes import Kind, describe, emoji

FETCHED = datetime(2026, 10, 30, 9, 15, tzinfo=UTC)
ANSI = re.compile(r"\x1b\[")


def result(origin: Origin = Origin.LIVE, **overrides: object) -> FetchResult:
    payload = forecast_payload(current_overrides=dict(overrides) if overrides else None)
    report = parse_forecast(payload, yerevan_location(), FETCHED)
    return FetchResult(report, origin, report.current.time + timedelta(minutes=2))


def rich_text(res: FetchResult, view: ViewOptions, width: int = 100, color: bool = False) -> str:
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, theme=THEME, force_terminal=color, color_system="truecolor"
        if color else None, emoji=False,
    )  # fmt: skip
    console.print(render_rich(res, view, width))
    return buffer.getvalue()


# --------------------------------------------------------------------------- JSON


def test_json_metric_has_explicit_units_and_metadata() -> None:
    data = json.loads(render_json(result(), ViewOptions(forecast=True, hours=6)))
    assert data["mode"] == "live"
    assert data["units"] == {
        "temperature": "°C", "wind_speed": "km/h", "precipitation": "mm",
        "pressure": "hPa", "humidity": "%",
    }  # fmt: skip
    assert data["current"]["temperature"] == 14.6
    assert data["current"]["humidity_pct"] == 61
    assert data["current"]["wind_direction"] == "W"
    assert data["location"]["timezone"] == "Asia/Yerevan"
    assert data["source"]["attribution"].startswith("Weather data by Open-Meteo.com")
    assert data["source"]["age_seconds"] >= 0
    assert len(data["hourly"]) == 6
    assert len(data["daily"]) == 5


def test_json_imperial_converts_once() -> None:
    data = json.loads(render_json(result(), ViewOptions(units=UnitSystem.IMPERIAL, forecast=True)))
    assert data["units"]["temperature"] == "°F"
    assert data["current"]["temperature"] == pytest.approx(58.3, abs=0.05)  # 14.6 °C
    assert data["current"]["wind_speed"] == pytest.approx(7.7, abs=0.05)  # 12.4 km/h
    assert data["daily"][0]["max"] == 68.0  # 20 °C
    assert data["daily"][3]["precipitation_sum"] == pytest.approx(0.49, abs=0.005)  # 12.5 mm


def test_json_is_pure_ascii_and_parseable() -> None:
    text = render_json(result(), ViewOptions(forecast=True))
    assert text.isascii()
    assert not ANSI.search(text)
    json.loads(text)


def test_json_omits_sections_not_requested() -> None:
    data = json.loads(render_json(result(), ViewOptions()))
    assert "hourly" not in data and "daily" not in data


def test_json_marks_demo_as_synthetic() -> None:
    data = json.loads(render_json(result(Origin.DEMO), ViewOptions()))
    assert data["mode"] == "offline-synthetic-sample"
    assert data["source"]["synthetic"] is True


def test_json_nulls_for_missing_values() -> None:
    data = json.loads(render_json(result(relative_humidity_2m=None), ViewOptions()))
    assert data["current"]["humidity_pct"] is None


# --------------------------------------------------------------------------- plain


def test_plain_is_ascii_without_escape_codes() -> None:
    text = render_plain(result(), ViewOptions(forecast=True, hours=24))
    assert text.isascii()
    assert not ANSI.search(text)
    assert "14.6 C" in text
    assert "Humidity    : 61%" in text
    assert "12.4 km/h from W" in text
    assert "5-day forecast" in text
    assert "Next 24 hours" in text


def test_plain_fahrenheit_labels() -> None:
    text = render_plain(result(), ViewOptions(units=UnitSystem.IMPERIAL))
    assert "58.3 F" in text and "mph" in text
    assert " C " not in text


def test_plain_shows_na_for_missing_optional_values() -> None:
    text = render_plain(result(relative_humidity_2m=None, pressure_msl=None), ViewOptions())
    assert "Humidity    : N/A" in text
    assert "Pressure    : N/A" in text


def test_demo_output_is_conspicuously_labelled() -> None:
    text = render_plain(result(Origin.DEMO), ViewOptions())
    assert "[DEMO / SAMPLE DATA]" in text
    assert "NOT live weather" in text


def test_stale_output_is_labelled_with_age() -> None:
    res = result(Origin.STALE)
    res = replace(res, as_of=res.report.fetched_at + timedelta(minutes=42))
    text = render_plain(res, ViewOptions())
    assert "[STALE]" in text and "42 min ago" in text
    assert "could not be reached" in text


def test_postcode_is_shown_in_location_line() -> None:
    res = result()
    loc = replace(res.report.location, postcode="0010")
    res = replace(res, report=replace(res.report, location=loc))
    assert "postal code 0010" in render_plain(res, ViewOptions())


def test_notes_are_printed() -> None:
    text = render_plain(result(), ViewOptions(notes=("Showing Paris, France",)))
    assert "Note: Showing Paris, France" in text


# --------------------------------------------------------------------------- Rich


@pytest.mark.parametrize("width", [40, 60, 80, 100, 140])
def test_rich_renders_at_many_widths_without_overflow(width: int) -> None:
    text = rich_text(result(), ViewOptions(forecast=True, hours=24), width=width)
    assert "Yerevan" in text
    from rich.cells import cell_len

    assert max(cell_len(line) for line in text.splitlines()) <= width


def test_rich_shows_core_fields_with_units() -> None:
    text = rich_text(result(), ViewOptions(forecast=True))
    for expected in ("14.6°C", "Feels like", "61%", "12.4 km/h from W", "1018 hPa (sea level)",
                     "Overcast", "LIVE", "5-day forecast", "Next 24 hours"):  # fmt: skip
        assert expected in text, expected


def test_rich_imperial() -> None:
    text = rich_text(result(), ViewOptions(units=UnitSystem.IMPERIAL))
    assert "58.3°F" in text and "7.7 mph" in text


def test_rich_ascii_only_mode_has_no_emoji_or_box_drawing() -> None:
    text = rich_text(result(), ViewOptions(forecast=True, hours=6, ascii_only=True))
    assert text.isascii(), [c for c in text if not c.isascii()][:5]


def test_rich_color_output_uses_ansi_only_when_forced() -> None:
    assert not ANSI.search(rich_text(result(), ViewOptions()))
    assert ANSI.search(rich_text(result(), ViewOptions(), color=True))


def test_rich_demo_and_cache_badges() -> None:
    assert "DEMO / SAMPLE DATA" in rich_text(result(Origin.DEMO), ViewOptions())
    assert "CACHED" in rich_text(result(Origin.CACHE), ViewOptions())


def test_unknown_weather_code_renders_neutral_text() -> None:
    text = rich_text(result(weather_code=42), ViewOptions())
    assert "Unknown conditions" in text


# --------------------------------------------------------------------------- helpers


def test_formatter_handles_missing_values() -> None:
    fmt = Formatter(UnitSystem.METRIC, ascii_only=False)
    assert fmt.temp(None) == fmt.speed(None) == fmt.pct(None) == fmt.precip(None) == "N/A"
    assert fmt.temp(-3.25) == "-3.2°C"


def test_day_labels() -> None:
    today = datetime(2026, 10, 30).date()
    assert day_label(today, today) == "Today"
    assert day_label(today + timedelta(days=1), today) == "Tomorrow"
    assert day_label(today + timedelta(days=2), today) == "Sun 01 Nov"
    assert day_label(today + timedelta(days=2), today, short=True) == "Sun 01"


@pytest.mark.parametrize(
    ("seconds", "text"), [(10, "just now"), (300, "5 min ago"), (3900, "1 h 5 min ago")]
)
def test_age_text(seconds: int, text: str) -> None:
    assert age_text(timedelta(seconds=seconds)) == text


def test_weather_code_mapping() -> None:
    assert describe(0).description == "Clear sky"
    assert describe(95).kind is Kind.THUNDER
    assert describe(None).description == "Unknown conditions"
    assert emoji(Kind.CLEAR, is_day=False) != emoji(Kind.CLEAR, is_day=True)


# --------------------------------------------------------------------------- charts


def test_sparkline_flat_negative_and_missing() -> None:
    assert charts.sparkline([5, 5, 5]) == "▄▄▄"
    assert charts.sparkline([-10, 0, 10]) == "▁▅█"
    assert charts.sparkline([1, None, 3]) == "▁ █"
    assert charts.sparkline([None, None]) == "  "
    assert charts.sparkline([]) == ""


def test_sparkline_ascii_and_absolute_span() -> None:
    assert charts.sparkline([0, 100], ascii_only=True) == "_#"
    assert charts.sparkline([10, 10], span=(0, 100)) == "▂▂"


def test_range_bar_positions() -> None:
    assert charts.range_bar(0, 10, 0, 10, 5) == "━━━━━"
    assert charts.range_bar(5, 5, 0, 10, 5) == "··━··"
    assert charts.range_bar(1, 1, 1, 1, 4, ascii_only=True) == "===="
    assert charts.range_bar(0, 1, 0, 1, 0) == ""
    assert charts.range_bar(8, 2, 0, 10, 6) == charts.range_bar(2, 8, 0, 10, 6)


def test_column_chart_shapes() -> None:
    rows = charts.column_chart([0, 5, 10], height=2)
    assert len(rows) == 2 and all(len(r) == 3 for r in rows)
    assert rows[0][2] == "█" and rows[1][2] == "█"
    assert rows[0][0] == " " and rows[1][0] != " "  # minimum still visible
    flat = charts.column_chart([3, 3], height=2)
    assert flat[0] == flat[0][0] * 2
    assert charts.column_chart([None, None], height=1) == ["  "]
    ascii_rows = charts.column_chart([0, 10], height=1, ascii_only=True)
    assert ascii_rows == [" #"]
