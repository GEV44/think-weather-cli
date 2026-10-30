"""Pure unit conversions and labels. No provider, network or rendering coupling.

Canonical (stored) units are Celsius, km/h, mm and hPa. These helpers convert a
canonical value for display only, so a value is never converted twice.
"""

from __future__ import annotations

from weather_cli.models import UnitSystem

KM_PER_MILE = 1.609344
MM_PER_INCH = 25.4

_COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)  # fmt: skip


def _system(units: UnitSystem | str) -> UnitSystem:
    return units if isinstance(units, UnitSystem) else UnitSystem.parse(units)


def temperature(value_c: float, units: UnitSystem | str) -> float:
    """Convert a Celsius value to the requested system (Fahrenheit for imperial)."""
    return value_c * 9 / 5 + 32 if _system(units) is UnitSystem.IMPERIAL else value_c


def wind(value_kmh: float, units: UnitSystem | str) -> float:
    """Convert a km/h value to the requested system (mph for imperial)."""
    return value_kmh / KM_PER_MILE if _system(units) is UnitSystem.IMPERIAL else value_kmh


def precipitation(value_mm: float, units: UnitSystem | str) -> float:
    """Convert millimetres to the requested system (inches for imperial)."""
    return value_mm / MM_PER_INCH if _system(units) is UnitSystem.IMPERIAL else value_mm


def temperature_unit(units: UnitSystem) -> str:
    return "°F" if units is UnitSystem.IMPERIAL else "°C"


def speed_unit(units: UnitSystem) -> str:
    return "mph" if units is UnitSystem.IMPERIAL else "km/h"


def precipitation_unit(units: UnitSystem) -> str:
    return "in" if units is UnitSystem.IMPERIAL else "mm"


def compass(degrees: float) -> str:
    """Meteorological direction the wind blows *from*, as a 16-point compass label."""
    index = round((degrees % 360) / 22.5) % 16
    return _COMPASS_POINTS[index]
