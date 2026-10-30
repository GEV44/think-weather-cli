"""The narrow capabilities the service depends on (real, demo and test doubles)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from weather_cli.models import Location, WeatherReport


@dataclass(frozen=True, slots=True)
class GeoMatch:
    """A geocoder candidate plus the hints used to rank and disambiguate it."""

    location: Location
    populated_place: bool = True
    postcodes: tuple[str, ...] = ()


class Geocoder(Protocol):
    def search(self, name: str, country_code: str | None, count: int = 10) -> list[GeoMatch]: ...


class PostalLookup(Protocol):
    def supports(self, country_code: str) -> bool: ...

    def lookup(self, country_code: str, postcode: str) -> list[GeoMatch]: ...


class Forecaster(Protocol):
    cache_namespace: str

    def fetch(self, location: Location, fetched_at: datetime) -> WeatherReport: ...
