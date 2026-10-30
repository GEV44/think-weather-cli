"""Optional postal-code fallback using Zippopotam.us, only for countries it lists.

Used when Open-Meteo's geocoder does not index a postcode. Countries outside the
published list (Armenia included, as of October 2026) are never queried and no
coordinates are invented for them.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from weather_cli.config import ZIPPOPOTAM_URL
from weather_cli.http import HttpClient
from weather_cli.models import Location
from weather_cli.providers import values as v
from weather_cli.providers.base import GeoMatch

SERVICE = "Zippopotam.us postal lookup"

# Country codes listed on https://api.zippopotam.us/ (checked 2026-10-30).
SUPPORTED_COUNTRIES = frozenset(
    """
    AD AR AS AT AU BD BE BG BR CA CH CZ
    DE DK DO ES FI FO FR GB GF GG GL GP
    GT GU GY HR HU IM IN IS IT JE JP LI
    LK LT LU MC MD MH MK MP MQ MX MY NL
    NO NZ PH PK PL PM PR PT RE RU SE SI
    SJ SK SM TH TR US VA VI YT ZA
    """.split()
)


def district_prefix(country_code: str, postcode: str) -> str | None:
    """Return the district of a full GB, CA or NL postcode, if valid."""
    normalized = " ".join(postcode.upper().split())
    country = country_code.upper()
    if country == "GB":
        if " " in normalized:
            outward, inward = normalized.split(" ", 1)
        elif 5 <= len(normalized) <= 7:
            outward, inward = normalized[:-3], normalized[-3:]
        else:
            return None
        if re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?", outward) and re.fullmatch(r"\d[A-Z]{2}", inward):
            return outward
    elif country == "CA" and re.fullmatch(r"[A-Z]\d[A-Z] ?\d[A-Z]\d", normalized):
        return normalized[:3]
    elif country == "NL" and re.fullmatch(r"\d{4} ?[A-Z]{2}", normalized):
        return normalized[:4]
    return None


def parse_postal(payload: Any, postcode: str) -> list[GeoMatch]:
    if not isinstance(payload, dict):
        return []
    places = payload.get("places")
    if not isinstance(places, list):
        return []
    country = v.text(payload.get("country"))
    country_code = v.text(payload.get("country abbreviation"))
    matches = []
    for place in places:
        if not isinstance(place, dict):
            continue
        name = v.text(place.get("place name"))
        latitude = _coordinate(place.get("latitude"), 90.0)
        longitude = _coordinate(place.get("longitude"), 180.0)
        if name is None or latitude is None or longitude is None:
            continue
        location = Location(
            name=name,
            latitude=latitude,
            longitude=longitude,
            country=country,
            country_code=country_code.upper() if country_code else None,
            admin1=v.text(place.get("state")),
            postcode=postcode,
            resolved_by="zippopotam.us",
        )
        matches.append(GeoMatch(location, populated_place=True, postcodes=(postcode,)))
    return matches


def _coordinate(value: Any, limit: float) -> float | None:
    """Zippopotam returns coordinates as strings."""
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    return v.bounded(value, -limit, limit)


class ZippopotamPostalLookup:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def supports(self, country_code: str) -> bool:
        return country_code.upper() in SUPPORTED_COUNTRIES

    def lookup(self, country_code: str, postcode: str) -> list[GeoMatch]:
        if not self.supports(country_code):
            return []
        matches = self._lookup(country_code, postcode)
        prefix = district_prefix(country_code, postcode)
        if not matches and prefix is not None:
            return self._lookup(country_code, prefix)
        return matches

    def _lookup(self, country_code: str, postcode: str) -> list[GeoMatch]:
        url = f"{ZIPPOPOTAM_URL}/{quote(country_code.lower(), safe='')}/{quote(postcode, safe='')}"
        payload = self._http.get_json(url, service=SERVICE, not_found_ok=True)
        return parse_postal(payload, postcode)
