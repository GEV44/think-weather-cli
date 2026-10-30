"""Use cases: resolve a location, then get a weather report through the cache.

The service knows nothing about argparse, Rich or JSON field names of any API.
Collaborators are injected, so tests and the offline demo reuse this exact path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum

from weather_cli import codec
from weather_cli.cache import CacheEntry, SQLiteCache
from weather_cli.config import GEOCODE_TTL, STALE_GRACE, WEATHER_TTL
from weather_cli.errors import (
    AmbiguousLocationError,
    InputError,
    LocationNotFoundError,
    ProviderError,
)
from weather_cli.locations import LocationQuery, Resolution, disambiguate
from weather_cli.models import FetchResult, Location, Origin, WeatherReport
from weather_cli.providers.base import Forecaster, Geocoder, GeoMatch, PostalLookup
from weather_cli.providers.postal import district_prefix

log = logging.getLogger(__name__)

Chooser = Callable[[Sequence[Location]], Location]
Clock = Callable[[], datetime]

GEOCODE_NAMESPACE = "geocode-v2"
SEARCH_COUNT = 10
QUALIFIED_SEARCH_COUNT = 100
""""City, Region" filters locally, so fetch enough rivals to include small namesakes."""
POSTAL_NAMESPACE = "postal-v1"


class CacheMode(StrEnum):
    NORMAL = "normal"
    REFRESH = "refresh"
    """Skip reading cached *weather* (locations still cached); write fresh results."""
    OFF = "off"
    """Neither read nor write."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class WeatherService:
    def __init__(
        self,
        geocoder: Geocoder,
        forecaster: Forecaster,
        *,
        postal: PostalLookup | None = None,
        cache: SQLiteCache | None = None,
        cache_mode: CacheMode = CacheMode.NORMAL,
        clock: Clock = utc_now,
        origin: Origin = Origin.LIVE,
    ) -> None:
        self._geocoder = geocoder
        self._forecaster = forecaster
        self._postal = postal
        self._cache = cache if cache_mode is not CacheMode.OFF else None
        self._cache_mode = cache_mode
        self._clock = clock
        self._origin = origin

    # -- locations -----------------------------------------------------------------

    def resolve(self, query: LocationQuery, choose: Chooser | None = None) -> Resolution:
        """Resolve to exactly one location, asking ``choose`` when ambiguous.

        Without a chooser (one-shot mode) ambiguity raises with the candidates.
        """
        if query.is_postcode and not query.country_code:
            raise InputError(
                f"'{query.text}' looks like a postal code, which needs a country.",
                f"Postal codes repeat across countries. Try: weather {query.text} --country US",
            )
        resolution = disambiguate(query, self._search(query))
        if (
            query.is_postcode
            and not resolution.exact  # no geocoder result actually lists this postcode
            and query.country_code
            and self._postal is not None
            and self._postal.supports(query.country_code)
        ):
            try:
                postal = self._postal_resolution(query)
            except ProviderError:
                if resolution.location is None and not resolution.candidates:
                    raise
                log.debug("postal fallback unavailable; keeping the geocoder's closest match")
            else:
                if postal.location is not None or postal.candidates:
                    resolution = postal

        if resolution.location is None and not resolution.candidates:
            raise self._not_found(query)
        if resolution.location is None:
            if choose is None:
                raise self._ambiguous(query, resolution.candidates)
            picked = choose(resolution.candidates)
            resolution = replace(resolution, location=picked)
        assert resolution.location is not None
        if query.is_postcode:
            resolution = replace(
                resolution, location=replace(resolution.location, postcode=query.name)
            )
        return resolution

    def _search(self, query: LocationQuery) -> list[GeoMatch]:
        count = QUALIFIED_SEARCH_COUNT if query.qualifiers else SEARCH_COUNT
        return self._cached_matches(
            GEOCODE_NAMESPACE,
            f"{query.name.casefold()}|{query.country_code or '*'}|{count}",
            lambda: self._geocoder.search(query.name, query.country_code, count),
        )

    def _postal_resolution(self, query: LocationQuery) -> Resolution:
        matches = self._postal_lookup(query)
        prefix = district_prefix(query.country_code or "", query.name)
        if prefix is None or not matches or any(m.location.postcode != prefix for m in matches):
            return disambiguate(query, matches)

        district_query = replace(query, text=prefix, name=prefix)
        resolution = disambiguate(district_query, self._search(district_query))
        if not resolution.exact:
            postal = disambiguate(district_query, matches)
            if postal.location is not None or postal.candidates:
                resolution = postal
        return replace(
            resolution,
            exact=False,
            note=(
                f"Zippopotam.us only lists the district {prefix} for '{query.text}'; "
                "showing weather for that district."
            ),
        )

    def _postal_lookup(self, query: LocationQuery) -> list[GeoMatch]:
        assert self._postal is not None and query.country_code is not None
        postal, country = self._postal, query.country_code
        return self._cached_matches(
            POSTAL_NAMESPACE,
            f"{query.name}|{country}",
            lambda: postal.lookup(country, query.name),
        )

    def _cached_matches(
        self, namespace: str, key: str, search: Callable[[], list[GeoMatch]]
    ) -> list[GeoMatch]:
        now = self._clock()
        if self._cache is not None:
            entry = self._cache.get(namespace, key)
            if entry is not None and entry.is_fresh(now):
                try:
                    matches = codec.loads_matches(entry.payload)
                    log.debug("location cache hit for %s", namespace)
                    return matches
                except codec.CodecError as exc:
                    log.debug("ignoring unreadable cached location: %s", exc)
        matches = search()
        if self._cache is not None and matches:
            self._cache.put(namespace, key, codec.dumps_matches(matches), now=now, ttl=GEOCODE_TTL)
        return matches

    @staticmethod
    def _not_found(query: LocationQuery) -> LocationNotFoundError:
        where = f" in {query.country_code}" if query.country_code else ""
        if query.is_postcode:
            return LocationNotFoundError(
                f"Postal code '{query.text}' was not found{where}.",
                "Postal-code coverage differs by country and is not complete. Check the code, "
                "or search by city instead, e.g. weather Yerevan --country AM",
            )
        if query.country_code:
            hint = "Check the spelling, or try a different (or no) --country."
        else:
            hint = "Check the spelling, or add a country, e.g. --country GB."
        return LocationNotFoundError(f"No place called '{query.text}' was found{where}.", hint)

    @staticmethod
    def _ambiguous(query: LocationQuery, candidates: Sequence[Location]) -> AmbiguousLocationError:
        first = candidates[0]
        region = first.admin1 or first.country or "Region"
        country = first.country_code or "US"
        return AmbiguousLocationError(
            f"'{query.text}' matches {len(candidates)} different places.",
            candidates,
            f'Be more specific, e.g. weather "{first.name}, {region}" --country {country}',
        )

    # -- weather -------------------------------------------------------------------

    def fetch(self, location: Location, *, refresh: bool = False) -> FetchResult:
        """Fresh cached report if available, else a live one (stale only on outages)."""
        namespace = self._forecaster.cache_namespace
        key = f"{location.latitude:.4f},{location.longitude:.4f}"
        now = self._clock()
        entry = self._cache.get(namespace, key) if self._cache is not None else None

        read_cache = self._cache_mode is CacheMode.NORMAL and not refresh
        if entry is not None and entry.is_fresh(now) and read_cache:
            try:
                report = codec.loads_report(entry.payload)
                log.debug("weather cache hit (age %s)", entry.age(now))
                return FetchResult(_relabel(report, location), Origin.CACHE, now)
            except codec.CodecError as exc:
                log.debug("ignoring unreadable cached report: %s", exc)

        try:
            report = self._forecaster.fetch(location, now)
        except ProviderError as exc:
            stale = self._stale_fallback(exc, entry, location, now)
            if stale is None:
                raise
            return stale
        if self._cache is not None:
            self._cache.put(namespace, key, codec.dumps_report(report), now=now, ttl=WEATHER_TTL)
        as_of = report.current.time if self._origin is Origin.DEMO else now
        return FetchResult(report, self._origin, as_of)

    def _stale_fallback(
        self,
        error: ProviderError,
        entry: CacheEntry | None,
        location: Location,
        now: datetime,
    ) -> FetchResult | None:
        if not error.transient or entry is None or now - entry.expires_at > STALE_GRACE:
            return None
        try:
            report = codec.loads_report(entry.payload)
        except codec.CodecError:
            return None
        log.debug("provider unavailable; serving stale cached report")
        return FetchResult(_relabel(report, location), Origin.STALE, now)


def _relabel(report: WeatherReport, location: Location) -> WeatherReport:
    """Show the place the user asked for, even if the coordinates were cached via an alias."""
    merged = replace(location, timezone=location.timezone or report.location.timezone)
    return replace(report, location=merged)
