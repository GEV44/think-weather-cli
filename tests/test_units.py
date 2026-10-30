from __future__ import annotations

import pytest

from weather_cli import units
from weather_cli.models import UnitSystem


@pytest.mark.parametrize(
    ("celsius", "fahrenheit"),
    [(0, 32), (100, 212), (-40, -40), (37, 98.6), (-17.7777778, 0), (21.5, 70.7)],
)
def test_celsius_to_fahrenheit(celsius: float, fahrenheit: float) -> None:
    assert units.temperature(celsius, UnitSystem.IMPERIAL) == pytest.approx(fahrenheit, abs=1e-6)
    assert units.temperature(celsius, UnitSystem.METRIC) == celsius


@pytest.mark.parametrize(("kmh", "mph"), [(0, 0), (1.609344, 1), (100, 62.137119), (16.09344, 10)])
def test_kmh_to_mph(kmh: float, mph: float) -> None:
    assert units.wind(kmh, UnitSystem.IMPERIAL) == pytest.approx(mph, abs=1e-6)
    assert units.wind(kmh, "metric") == kmh


def test_mm_to_inches() -> None:
    assert units.precipitation(25.4, UnitSystem.IMPERIAL) == pytest.approx(1.0)
    assert units.precipitation(3.0, UnitSystem.METRIC) == 3.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("c", UnitSystem.METRIC),
        ("Celsius", UnitSystem.METRIC),
        (" metric ", UnitSystem.METRIC),
        ("F", UnitSystem.IMPERIAL),
        ("fahrenheit", UnitSystem.IMPERIAL),
        ("imperial", UnitSystem.IMPERIAL),
    ],
)
def test_unit_system_parse(text: str, expected: UnitSystem) -> None:
    assert UnitSystem.parse(text) is expected


def test_unit_system_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        UnitSystem.parse("kelvin")


def test_toggle_is_an_involution() -> None:
    assert UnitSystem.METRIC.toggled() is UnitSystem.IMPERIAL
    assert UnitSystem.METRIC.toggled().toggled() is UnitSystem.METRIC


def test_labels() -> None:
    assert units.temperature_unit(UnitSystem.IMPERIAL) == "°F"
    assert units.speed_unit(UnitSystem.METRIC) == "km/h"
    assert units.precipitation_unit(UnitSystem.IMPERIAL) == "in"


@pytest.mark.parametrize(
    ("degrees", "label"),
    [(0, "N"), (360, "N"), (11.24, "N"), (11.26, "NNE"), (45, "NE"), (180, "S"), (280, "W"),
     (315, "NW"), (348.75, "N"), (-90, "W")],
)  # fmt: skip
def test_compass(degrees: float, label: str) -> None:
    assert units.compass(degrees) == label
