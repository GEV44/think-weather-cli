"""Offline demo: synthetic sample data, intentionally NOT live observations.

The packaged fixture has the shape of Open-Meteo responses, so the demo goes
through the real parser, service and renderers; only the network is replaced.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from functools import cache
from importlib.resources import files
from typing import Any

from weather_cli.errors import LocationNotFoundError
from weather_cli.locations import fold
from weather_cli.models import Location, Origin, WeatherReport
from weather_cli.providers.base import GeoMatch
from weather_cli.providers.open_meteo import parse_forecast, parse_geocoding
from weather_cli.service import WeatherService

FIXTURE = "fixtures/yerevan_sample.json"
DEMO_NAMES = frozenset({"yerevan", fold("Երևան")})
DEFAULT_DEMO_LOCATION = "Yerevan"


@cache
def load_fixture() -> dict[str, Any]:
    text = files(__name__).joinpath(FIXTURE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


class DemoGeocoder:
    def search(self, name: str, country_code: str | None, count: int = 10) -> list[GeoMatch]:
        if fold(name) not in DEMO_NAMES or country_code not in (None, "AM"):
            raise LocationNotFoundError(
                "The offline demo only contains sample data for Yerevan, Armenia.",
                "Run 'weather Yerevan --demo', or drop --demo for live weather.",
            )
        return parse_geocoding(load_fixture()["geocoding"])


class DemoForecaster:
    cache_namespace = "demo"

    def fetch(self, location: Location, fetched_at: datetime) -> WeatherReport:
        report = parse_forecast(load_fixture()["forecast"], location, fetched_at)
        # The sample was "fetched" at its own sample time, not at wall-clock time.
        return replace(report, fetched_at=report.current.time)


def build_demo_service() -> WeatherService:
    return WeatherService(DemoGeocoder(), DemoForecaster(), cache=None, origin=Origin.DEMO)
