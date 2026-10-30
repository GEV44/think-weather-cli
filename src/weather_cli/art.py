"""Small original ASCII weather illustrations with Rich style markup.

Every glyph is plain ASCII so the same drawing works in ``--plain`` mode (with
the markup stripped). Styles are defined in :data:`weather_cli.rendering.THEME`.
"""

from __future__ import annotations

from weather_cli.weather_codes import Kind

_CLOUD = [
    "[cloud]     .--.     [/]",
    "[cloud]  .-(    ).   [/]",
    "[cloud] (___.__)__)  [/]",
]

ART: dict[str, list[str]] = {
    "sun": [
        "[sun]   \\  |  /     [/]",
        "[sun]    .---.      [/]",
        "[sun] --(     )--   [/]",
        "[sun]    '---'      [/]",
        "[sun]   /  |  \\     [/]",
    ],
    "moon": [
        "[moon]     .--.     [/]",
        "[moon]    /  .'     [/]",
        "[moon]   |  (       [/]",
        "[moon]    \\  '.     [/]",
        "[moon]     '--'     [/]",
    ],
    Kind.PARTLY_CLOUDY: [
        "[sun]  \\  |  /       [/]",
        "[sun] -- .-.[/][cloud]  .--.   [/]",
        "[sun]   (   [/][cloud].-(    ). [/]",
        "[sun]  /  '-[/][cloud](___.__)__)[/]",
        "",
    ],
    Kind.CLOUDY: ["", *_CLOUD, ""],
    Kind.FOG: [
        "",
        "[fog] _ - _ - _ - _ [/]",
        "[fog]  _ - _ - _ -  [/]",
        "[fog] _ - _ - _ - _ [/]",
        "",
    ],
    Kind.DRIZZLE: [*_CLOUD, "[rain]   ,  ,  ,  ,  [/]", "[rain]  ,  ,  ,  ,   [/]"],
    Kind.RAIN: [*_CLOUD, "[rain]  / / / / /   [/]", "[rain] / / / / /    [/]"],
    Kind.FREEZING: [
        *_CLOUD,
        "[rain]  /[/][snow]*[/][rain]/ /[/][snow]*[/][rain]/[/]",
        "[snow] * / * / *    [/]",
    ],
    Kind.SNOW: [*_CLOUD, "[snow]  *  *  *  *   [/]", "[snow]   *  *  *    [/]"],
    Kind.THUNDER: [*_CLOUD, "[bolt]    /_  /_     [/]", "[bolt]     /   /     [/]"],
    Kind.UNKNOWN: [
        "",
        "[cloud]    .--.     [/]",
        "[cloud]   (  ? )    [/]",
        "[cloud]    '--'     [/]",
        "",
    ],
}


def illustration(kind: Kind, is_day: bool | None) -> list[str]:
    """Markup lines for a condition, without blank padding lines."""
    key = ("moon" if is_day is False else "sun") if kind is Kind.CLEAR else kind
    return [line for line in ART[key] if line]
