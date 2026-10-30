"""Application constants and environment-driven settings."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from weather_cli import __version__

APP_NAME = "think-weather-cli"
USER_AGENT = f"{APP_NAME}/{__version__} (+python-httpx)"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ZIPPOPOTAM_URL = "https://api.zippopotam.us"

ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0) | Geocoding based on GeoNames"

# Cache policy (documented in README / docs/architecture.md).
GEOCODE_TTL = timedelta(hours=24)
WEATHER_TTL = timedelta(minutes=10)
STALE_GRACE = timedelta(minutes=60)
"""How long past expiry a cached report may be shown when the provider is down."""

FORECAST_DAYS_REQUESTED = 6
"""Six local days guarantee five calendar rows and >= 24 upcoming hours at any time of day."""
FORECAST_DAYS_SHOWN = 5
DEFAULT_HOURS = 24
MAX_HOURS = 48

CACHE_DIR_ENV = "THINK_WEATHER_CACHE_DIR"


def cache_dir() -> Path:
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return Path(override)
    from platformdirs import user_cache_dir

    return Path(user_cache_dir(APP_NAME, appauthor=False))
