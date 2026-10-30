"""Pure location-query rules: validation, postcode detection and disambiguation.

No I/O here. The rules, in order:

1. Validate the raw text locally (empty, too long, control characters, bad
   country code) so obviously bad input never costs a request.
2. ``"City, Region"`` splits into a geocoder name plus qualifiers matched
   against region/country, because the geocoder itself does not understand
   commas.
3. Text that looks like a postcode is never assigned a country by guessing:
   the caller must supply one (``--country`` or an interactive prompt).
4. Among geocoder results, exact name matches beat partial ones and populated
   places beat parks or airports. If several distinct places remain, one is
   chosen only when it is overwhelmingly larger than every alternative (e.g.
   Paris, France vs. Paris, Texas) and the choice is reported to the user;
   otherwise the result is *ambiguous* and the caller must ask or fail.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from weather_cli.errors import InputError
from weather_cli.models import Location
from weather_cli.providers.base import GeoMatch

MAX_QUERY_LENGTH = 100
DOMINANCE_RATIO = 20
"""A place is picked automatically only if it is 20x more populous than any rival."""

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_POSTCODE_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9 -]{1,9}$")
_WHITESPACE_RE = re.compile(r"\s+")
_NEAR_DEGREES = 0.05  # ~5 km: treat results this close with the same label as one place


@dataclass(frozen=True, slots=True)
class LocationQuery:
    text: str
    """The user's text as typed (trimmed); postcodes keep their leading zeros."""
    name: str
    """What is sent to the geocoder."""
    qualifiers: tuple[str, ...] = ()
    country_code: str | None = None
    is_postcode: bool = False


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of disambiguation: one location, or several to choose from."""

    location: Location | None
    candidates: tuple[Location, ...] = ()
    note: str | None = None
    exact: bool = True
    alternatives: tuple[Location, ...] = ()


def fold(value: str) -> str:
    """Case- and accent-insensitive comparison key ("Zürich" == "zurich")."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RE.sub(" ", stripped).strip()


def normalize_country(country: str | None) -> str | None:
    if country is None:
        return None
    value = country.strip()
    if not value:
        return None
    if not _COUNTRY_RE.match(value):
        raise InputError(
            f"'{country}' is not a two-letter country code.",
            "Use an ISO 3166-1 alpha-2 code such as US, AM, DE or GB.",
        )
    return value.upper()


def looks_like_postcode(text: str) -> bool:
    return bool(_POSTCODE_RE.match(text))


def parse_query(text: str, country: str | None = None) -> LocationQuery:
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    if not cleaned:
        raise InputError(
            "Please enter a city name or a postal code.",
            "Example: weather Yerevan   or   weather 90210 --country US",
        )
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise InputError(
            f"That location is too long ({len(cleaned)} characters, max {MAX_QUERY_LENGTH}).",
            "Enter just the city name or postal code.",
        )
    if any(unicodedata.category(c).startswith("C") for c in cleaned):
        raise InputError(
            "The location contains control characters.", "Type the place name as plain text."
        )
    country_code = normalize_country(country)
    if looks_like_postcode(cleaned):
        return LocationQuery(cleaned, cleaned.upper(), (), country_code, is_postcode=True)

    name, *rest = (part.strip() for part in cleaned.split(","))
    qualifiers = tuple(part for part in rest if part)
    if len(fold(name)) < 2 or not any(c.isalpha() for c in name):
        raise InputError(
            f"'{cleaned}' does not look like a place name.",
            "Use at least two letters, e.g. 'Yerevan', or a postal code with --country.",
        )
    return LocationQuery(cleaned, name, qualifiers, country_code)


def _matches_qualifier(location: Location, qualifier: str) -> bool:
    wanted = fold(qualifier)
    fields = (location.admin1, location.admin2, location.country, location.country_code)
    return any(value and fold(value).startswith(wanted) for value in fields)


def _same_place(a: Location, b: Location) -> bool:
    return (
        fold(a.name) == fold(b.name)
        and (a.admin1 or "") == (b.admin1 or "")
        and a.country_code == b.country_code
        and abs(a.latitude - b.latitude) < _NEAR_DEGREES
        and abs(a.longitude - b.longitude) < _NEAR_DEGREES
    )


def _dedupe(locations: Sequence[Location]) -> list[Location]:
    unique: list[Location] = []
    for location in locations:
        if not any(_same_place(location, kept) for kept in unique):
            unique.append(location)
    return unique


def _dominant(locations: Sequence[Location]) -> Location | None:
    ranked = sorted(locations, key=lambda loc: loc.population or 0, reverse=True)
    top, runner_up = ranked[0], ranked[1]
    top_population = top.population or 0
    if top_population and top_population >= DOMINANCE_RATIO * (runner_up.population or 0):
        return top
    return None


def disambiguate(query: LocationQuery, matches: Sequence[GeoMatch]) -> Resolution:
    """Choose a location from geocoder matches, or report the candidates."""
    if query.is_postcode:
        wanted = query.name.replace(" ", "")
        exact_codes = [
            m for m in matches if any(p.upper().replace(" ", "") == wanted for p in m.postcodes)
        ]
        pool = [m.location for m in (exact_codes or matches)]
        exact = bool(exact_codes)
    else:
        qualified = [
            m for m in matches if all(_matches_qualifier(m.location, q) for q in query.qualifiers)
        ]
        populated = [m for m in qualified if m.populated_place] or qualified
        named = [m for m in populated if fold(m.location.name) == fold(query.name)]
        exact = bool(named)
        pool = [m.location for m in (named or populated)]

    candidates = _dedupe(pool)
    if not candidates:
        return Resolution(None, exact=False)
    if len(candidates) == 1:
        note = None if exact else f"Closest match for '{query.text}': {candidates[0].label}."
        return Resolution(candidates[0], tuple(candidates), note, exact)
    if exact and not query.is_postcode:
        winner = _dominant(candidates)
        if winner is not None:
            others = tuple(c for c in candidates if c is not winner)
            note = (
                f"Showing {winner.label}; {len(others)} other place(s) share this name "
                "(use --country or 'City, Region' to choose another)."
            )
            return Resolution(winner, tuple(candidates), note, exact, others)
    return Resolution(None, tuple(candidates), exact=exact)
