"""Provider-neutral, immutable domain models.

All measurements are stored in canonical metric units (degrees Celsius, km/h,
hPa, mm). Conversion to the user's preferred units happens only when rendering.
All timestamps are timezone-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class UnitSystem(StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"

    @classmethod
    def parse(cls, value: str) -> UnitSystem:
        """Accept the spellings people actually type: ``c``, ``celsius``, ``imperial``..."""
        normalized = value.strip().casefold()
        if normalized in {"c", "celsius", "metric", "si"}:
            return cls.METRIC
        if normalized in {"f", "fahrenheit", "imperial", "us"}:
            return cls.IMPERIAL
        raise ValueError(f"unknown unit system: {value!r}")

    def toggled(self) -> UnitSystem:
        return UnitSystem.IMPERIAL if self is UnitSystem.METRIC else UnitSystem.METRIC


class Origin(StrEnum):
    """Where the report shown to the user came from."""

    LIVE = "live"
    CACHE = "cached"
    STALE = "stale-cache"
    DEMO = "offline-synthetic-sample"


@dataclass(frozen=True, slots=True)
class Location:
    name: str
    latitude: float
    longitude: float
    country: str | None = None
    country_code: str | None = None
    admin1: str | None = None
    admin2: str | None = None
    timezone: str | None = None
    elevation_m: float | None = None
    population: int | None = None
    postcode: str | None = None
    provider_id: int | None = None
    resolved_by: str = "open-meteo-geocoding"

    @property
    def label(self) -> str:
        """Human label such as ``Beverly Hills, California, United States``."""
        parts = [self.name]
        if self.admin1 and self.admin1.casefold() != self.name.casefold():
            parts.append(self.admin1)
        parts.append(self.country or self.country_code or "")
        return ", ".join(part for part in parts if part)

    @property
    def detail(self) -> str:
        """A longer description used to tell candidates apart."""
        bits = [self.label]
        if self.admin2 and self.admin2 not in self.label:
            bits.append(f"({self.admin2})")
        bits.append(f"[{self.latitude:.3f}, {self.longitude:.3f}]")
        if self.population:
            bits.append(f"pop. {self.population:,}")
        return " ".join(bits)


@dataclass(frozen=True, slots=True)
class CurrentConditions:
    time: datetime
    temperature_c: float
    weather_code: int | None
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    wind_direction_deg: float | None = None
    wind_gusts_kmh: float | None = None
    cloud_cover_pct: float | None = None
    pressure_msl_hpa: float | None = None
    precipitation_mm: float | None = None
    is_day: bool | None = None


@dataclass(frozen=True, slots=True)
class HourlyPoint:
    time: datetime
    temperature_c: float
    weather_code: int | None = None
    apparent_temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    precipitation_probability_pct: float | None = None
    wind_speed_kmh: float | None = None
    is_day: bool | None = None


@dataclass(frozen=True, slots=True)
class DayForecast:
    date: date
    temperature_max_c: float
    temperature_min_c: float
    weather_code: int | None = None
    precipitation_probability_max_pct: float | None = None
    precipitation_sum_mm: float | None = None
    wind_speed_max_kmh: float | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None


@dataclass(frozen=True, slots=True)
class WeatherReport:
    """One normalized provider response: current + hourly + daily for a location."""

    location: Location
    timezone: str
    timezone_abbreviation: str | None
    current: CurrentConditions
    hourly: tuple[HourlyPoint, ...]
    daily: tuple[DayForecast, ...]
    fetched_at: datetime
    provider: str = "Open-Meteo"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A report plus how it was obtained, for honest provenance labels."""

    report: WeatherReport
    origin: Origin
    as_of: datetime
    """Reference "now" for selecting upcoming hours (sample time for the demo)."""
