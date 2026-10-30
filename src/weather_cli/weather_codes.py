"""WMO weather interpretation codes (as documented by Open-Meteo) in one place.

Each code maps to readable text plus a visual *kind*. Icons are always shown
next to the text, so no information is conveyed by glyph or colour alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    CLEAR = "clear"
    PARTLY_CLOUDY = "partly-cloudy"
    CLOUDY = "cloudy"
    FOG = "fog"
    DRIZZLE = "drizzle"
    RAIN = "rain"
    FREEZING = "freezing"
    SNOW = "snow"
    THUNDER = "thunder"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Condition:
    code: int | None
    description: str
    kind: Kind


_CODES: dict[int, tuple[str, Kind]] = {
    0: ("Clear sky", Kind.CLEAR),
    1: ("Mainly clear", Kind.CLEAR),
    2: ("Partly cloudy", Kind.PARTLY_CLOUDY),
    3: ("Overcast", Kind.CLOUDY),
    45: ("Fog", Kind.FOG),
    48: ("Depositing rime fog", Kind.FOG),
    51: ("Light drizzle", Kind.DRIZZLE),
    53: ("Moderate drizzle", Kind.DRIZZLE),
    55: ("Dense drizzle", Kind.DRIZZLE),
    56: ("Light freezing drizzle", Kind.FREEZING),
    57: ("Dense freezing drizzle", Kind.FREEZING),
    61: ("Slight rain", Kind.RAIN),
    63: ("Moderate rain", Kind.RAIN),
    65: ("Heavy rain", Kind.RAIN),
    66: ("Light freezing rain", Kind.FREEZING),
    67: ("Heavy freezing rain", Kind.FREEZING),
    71: ("Slight snowfall", Kind.SNOW),
    73: ("Moderate snowfall", Kind.SNOW),
    75: ("Heavy snowfall", Kind.SNOW),
    77: ("Snow grains", Kind.SNOW),
    80: ("Slight rain showers", Kind.RAIN),
    81: ("Moderate rain showers", Kind.RAIN),
    82: ("Violent rain showers", Kind.RAIN),
    85: ("Slight snow showers", Kind.SNOW),
    86: ("Heavy snow showers", Kind.SNOW),
    95: ("Thunderstorm", Kind.THUNDER),
    96: ("Thunderstorm with slight hail", Kind.THUNDER),
    99: ("Thunderstorm with heavy hail", Kind.THUNDER),
}

UNKNOWN_DESCRIPTION = "Unknown conditions"


def describe(code: int | None) -> Condition:
    """Map a WMO code to a condition; unknown or missing codes are neutral."""
    if code is not None and code in _CODES:
        text, kind = _CODES[code]
        return Condition(code, text, kind)
    return Condition(code, UNKNOWN_DESCRIPTION, Kind.UNKNOWN)


# Emoji with an explicit variation selector so modern terminals draw them two
# cells wide, matching Rich's width calculation.
_EMOJI: dict[Kind, str] = {
    Kind.CLEAR: "☀️",  # sun
    Kind.PARTLY_CLOUDY: "⛅",  # sun behind cloud
    Kind.CLOUDY: "☁️",  # cloud
    Kind.FOG: "\U0001f32b️",  # fog
    Kind.DRIZZLE: "\U0001f326️",  # sun behind rain cloud
    Kind.RAIN: "\U0001f327️",  # cloud with rain
    Kind.FREEZING: "\U0001f9ca",  # ice
    Kind.SNOW: "❄️",  # snowflake
    Kind.THUNDER: "⛈️",  # cloud with lightning and rain
    Kind.UNKNOWN: "❔",  # white question mark
}
_NIGHT_CLEAR = "\U0001f319"  # crescent moon

_ASCII: dict[Kind, str] = {
    Kind.CLEAR: "(*)",
    Kind.PARTLY_CLOUDY: "(*~",
    Kind.CLOUDY: "~~~",
    Kind.FOG: "===",
    Kind.DRIZZLE: "~,~",
    Kind.RAIN: "~/~",
    Kind.FREEZING: "~/*",
    Kind.SNOW: "*.*",
    Kind.THUNDER: "~!~",
    Kind.UNKNOWN: "???",
}


def emoji(kind: Kind, is_day: bool | None = True) -> str:
    if kind is Kind.CLEAR and is_day is False:
        return _NIGHT_CLEAR
    return _EMOJI[kind]


def ascii_symbol(kind: Kind) -> str:
    return _ASCII[kind]
