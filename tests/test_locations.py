from __future__ import annotations

import pytest

from weather_cli.errors import InputError
from weather_cli.locations import (
    LocationQuery,
    disambiguate,
    fold,
    looks_like_postcode,
    normalize_country,
    parse_query,
)
from weather_cli.models import Location
from weather_cli.providers.base import GeoMatch


def match(
    name: str,
    *,
    admin1: str | None = None,
    cc: str = "US",
    population: int | None = None,
    lat: float = 10.0,
    lon: float = 10.0,
    populated: bool = True,
    postcodes: tuple[str, ...] = (),
) -> GeoMatch:
    location = Location(
        name, lat, lon, country="Testland", country_code=cc, admin1=admin1, population=population
    )
    return GeoMatch(location, populated, postcodes)


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_empty_input_is_rejected_locally(text: str) -> None:
    with pytest.raises(InputError, match="city name or a postal code"):
        parse_query(text)


def test_overlong_input_is_rejected() -> None:
    with pytest.raises(InputError, match="too long"):
        parse_query("a" * 101)


def test_control_characters_are_rejected() -> None:
    with pytest.raises(InputError, match="control characters"):
        parse_query("Yere\x00van")


@pytest.mark.parametrize("text", ["!!!", "1", "-", "x"])
def test_clearly_invalid_names(text: str) -> None:
    with pytest.raises(InputError):
        parse_query(text)


def test_whitespace_is_trimmed_and_collapsed() -> None:
    query = parse_query("  New    York  ")
    assert query.name == "New York"
    assert not query.is_postcode


def test_unicode_city_names_are_supported() -> None:
    assert parse_query("Երևան").name == "Երևան"
    assert parse_query("Zürich").name == "Zürich"


def test_city_region_syntax() -> None:
    query = parse_query("Springfield, Illinois", "us")
    assert query.name == "Springfield"
    assert query.qualifiers == ("Illinois",)
    assert query.country_code == "US"


@pytest.mark.parametrize("code", ["00501", "0010", "90210", "10115", "SW1A 1AA", "K1A 0B1"])
def test_postcodes_are_detected_and_kept_as_strings(code: str) -> None:
    query = parse_query(code, "US")
    assert query.is_postcode
    assert query.name == code.upper()
    assert query.name.startswith(code[0].upper())  # leading zeros preserved


@pytest.mark.parametrize("text", ["Yerevan", "New York", "Saint-Étienne"])
def test_city_names_are_not_postcodes(text: str) -> None:
    assert not looks_like_postcode(text)


@pytest.mark.parametrize(
    ("raw", "expected"), [("am", "AM"), (" Us ", "US"), (None, None), ("", None)]
)
def test_country_normalization(raw: str | None, expected: str | None) -> None:
    assert normalize_country(raw) == expected


@pytest.mark.parametrize("raw", ["ARM", "U", "1A", "U.S."])
def test_invalid_country_codes(raw: str) -> None:
    with pytest.raises(InputError, match="two-letter"):
        normalize_country(raw)


def test_fold_ignores_case_and_accents() -> None:
    assert fold("Zürich") == fold("ZURICH")
    assert fold("  São   Paulo ") == "sao paulo"


# --------------------------------------------------------------------------- disambiguation


def query(text: str, country: str | None = None) -> LocationQuery:
    return parse_query(text, country)


def test_single_exact_match_resolves() -> None:
    result = disambiguate(query("Yerevan"), [match("Yerevan", population=1_000_000)])
    assert result.location is not None and result.location.name == "Yerevan"
    assert result.note is None


def test_exact_name_beats_partial_matches() -> None:
    result = disambiguate(
        query("Yerevan"),
        [match("Yerevan", population=1_144_700), match("Yerevani"), match("Yerevanly")],
    )
    assert result.location is not None and result.location.name == "Yerevan"


def test_comparable_places_are_ambiguous() -> None:
    result = disambiguate(
        query("Springfield"),
        [
            match("Springfield", admin1="Missouri", population=170_188, lat=37),
            match("Springfield", admin1="Illinois", population=114_394, lat=39),
        ],
    )
    assert result.location is None
    assert len(result.candidates) == 2


def test_overwhelmingly_larger_place_is_chosen_with_a_note() -> None:
    result = disambiguate(
        query("Paris"),
        [
            match("Paris", cc="FR", population=2_138_551, lat=48),
            match("Paris", admin1="Texas", population=24_782, lat=33),
        ],
    )
    assert result.location is not None and result.location.country_code == "FR"
    assert result.note is not None and "1 other place" in result.note
    assert len(result.alternatives) == 1


def test_unknown_populations_never_trigger_automatic_choice() -> None:
    result = disambiguate(query("Newtown"), [match("Newtown", lat=1), match("Newtown", lat=20)])
    assert result.location is None


def test_region_qualifier_filters_candidates() -> None:
    result = disambiguate(
        query("Springfield, Illinois"),
        [
            match("Springfield", admin1="Missouri", population=170_188, lat=37),
            match("Springfield", admin1="Illinois", population=114_394, lat=39),
        ],
    )
    assert result.location is not None and result.location.admin1 == "Illinois"


def test_qualifier_with_no_match_yields_nothing() -> None:
    result = disambiguate(query("Springfield, Atlantis"), [match("Springfield", admin1="Ohio")])
    assert result.location is None and not result.candidates


def test_populated_places_beat_parks_and_airports() -> None:
    result = disambiguate(
        query("Yerevan"),
        [match("Yerevan", population=100, lat=40), match("Yerevan", populated=False, lat=41)],
    )
    assert result.location is not None and result.location.latitude == 40


def test_near_duplicates_are_merged() -> None:
    result = disambiguate(
        query("Twin"), [match("Twin", lat=10.0, lon=10.0), match("Twin", lat=10.01, lon=10.01)]
    )
    assert result.location is not None


def test_single_partial_match_is_reported_as_closest() -> None:
    result = disambiguate(query("Beverly"), [match("Beverly Hills", population=10)])
    assert result.location is not None
    assert result.note is not None and "Closest match" in result.note
    assert result.exact is False


def test_postcode_prefers_results_listing_that_postcode() -> None:
    result = disambiguate(
        query("90210", "US"),
        [
            match("Somewhere", postcodes=("10001",), lat=1),
            match("Beverly Hills", postcodes=("90209", "90210"), lat=34),
        ],
    )
    assert result.location is not None and result.location.name == "Beverly Hills"


def test_postcode_with_several_unlisted_places_is_ambiguous() -> None:
    result = disambiguate(query("12345", "US"), [match("A", lat=1), match("B", lat=20)])
    assert result.location is None and len(result.candidates) == 2


def test_no_matches() -> None:
    assert disambiguate(query("Nowhere"), []).location is None
