"""Presentation: Rich dashboard, plain ASCII text and machine-readable JSON.

Renderers are pure functions of a :class:`FetchResult` and :class:`ViewOptions`.
They never fetch data, and they are the only place where canonical metric
values are converted to the user's units.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from rich import box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from weather_cli import charts, units
from weather_cli.art import illustration
from weather_cli.config import ATTRIBUTION, DEFAULT_HOURS, FORECAST_DAYS_SHOWN
from weather_cli.models import (
    CurrentConditions,
    DayForecast,
    FetchResult,
    HourlyPoint,
    Origin,
    UnitSystem,
    WeatherReport,
)
from weather_cli.weather_codes import ascii_symbol, describe, emoji

THEME = Theme(
    {
        "sun": "bold #ffb000",
        "moon": "#d9d98c",
        "cloud": "#9aa7b5",
        "rain": "#3fa7ff",
        "snow": "#8fd3ff",
        "bolt": "bold #ffd000",
        "fog": "#8a96a3",
        "label": "#8a96a3",
        "meta": "dim",
        "accent": "bold #4fc3f7",
        "warn": "bold #ff9e3d",
        "demo": "bold black on #ffb000",
        "stale": "bold black on #ff9e3d",
        "live": "bold #3ddc84",
        "cached": "bold #4fc3f7",
    }
)

_ORIGIN_BADGE = {
    Origin.LIVE: ("LIVE", "live"),
    Origin.CACHE: ("CACHED", "cached"),
    Origin.STALE: ("STALE", "stale"),
    Origin.DEMO: ("DEMO / SAMPLE DATA", "demo"),
}


@dataclass(frozen=True, slots=True)
class ViewOptions:
    units: UnitSystem = UnitSystem.METRIC
    forecast: bool = False
    hours: int | None = None
    notes: tuple[str, ...] = ()
    ascii_only: bool = False

    @property
    def hour_count(self) -> int:
        if self.hours is not None:
            return self.hours
        return DEFAULT_HOURS if self.forecast else 0


# --------------------------------------------------------------------------- selection


def upcoming_hours(points: Sequence[HourlyPoint], now: datetime, count: int) -> list[HourlyPoint]:
    """``count`` points starting with the hour that contains ``now``.

    Hourly data starts at local midnight, so the window is found by comparing
    aware datetimes (DST- and half-hour-offset-safe), not by slicing from index 0.
    """
    if count <= 0:
        return []
    start = 0
    for index, point in enumerate(points):
        if point.time <= now:
            start = index
        else:
            break
    return list(points[start : start + count])


def local_today(report: WeatherReport, now: datetime) -> date:
    zone = report.current.time.tzinfo
    return now.astimezone(zone).date()


def upcoming_days(
    report: WeatherReport, now: datetime, count: int = FORECAST_DAYS_SHOWN
) -> list[DayForecast]:
    today = local_today(report, now)
    return [day for day in report.daily if day.date >= today][:count]


def today_forecast(report: WeatherReport, now: datetime) -> DayForecast | None:
    today = local_today(report, now)
    return next((day for day in report.daily if day.date == today), None)


# --------------------------------------------------------------------------- formatting


class Formatter:
    """Unit conversion + labels for one view. ``None`` always renders as ``N/A``."""

    NA = "N/A"

    def __init__(self, system: UnitSystem, ascii_only: bool) -> None:
        self.system = system
        self.ascii_only = ascii_only

    @property
    def temp_unit(self) -> str:
        label = units.temperature_unit(self.system)
        return label.replace("°", " ") if self.ascii_only else label

    def temp_value(self, celsius: float | None) -> float | None:
        if celsius is None:
            return None
        return round(units.temperature(celsius, self.system), 1) + 0.0  # no "-0.0"

    def temp(self, celsius: float | None, *, unit: bool = True) -> str:
        value = self.temp_value(celsius)
        if value is None:
            return self.NA
        if not unit:
            return f"{value:.1f}" if self.ascii_only else f"{value:.1f}°"
        return f"{value:.1f}{self.temp_unit}"

    def speed_value(self, kmh: float | None) -> float | None:
        return None if kmh is None else round(units.wind(kmh, self.system), 1)

    def speed(self, kmh: float | None) -> str:
        value = self.speed_value(kmh)
        return self.NA if value is None else f"{value:.1f} {units.speed_unit(self.system)}"

    def precip_value(self, mm: float | None) -> float | None:
        if mm is None:
            return None
        digits = 2 if self.system is UnitSystem.IMPERIAL else 1
        return round(units.precipitation(mm, self.system), digits)

    def precip(self, mm: float | None) -> str:
        value = self.precip_value(mm)
        if value is None:
            return self.NA
        digits = 2 if self.system is UnitSystem.IMPERIAL else 1
        return f"{value:.{digits}f} {units.precipitation_unit(self.system)}"

    def pct(self, value: float | None) -> str:
        return self.NA if value is None else f"{round(value)}%"

    def wind(self, current: CurrentConditions) -> str:
        text = self.speed(current.wind_speed_kmh)
        if current.wind_direction_deg is not None and current.wind_speed_kmh is not None:
            text += f" from {units.compass(current.wind_direction_deg)}"
        return text

    def pressure(self, hpa: float | None) -> str:
        return self.NA if hpa is None else f"{hpa:.0f} hPa (sea level)"

    def icon(self, code: int | None, is_day: bool | None = True) -> str:
        kind = describe(code).kind
        return ascii_symbol(kind) if self.ascii_only else emoji(kind, is_day)


def clock(moment: datetime | None) -> str:
    return "N/A" if moment is None else moment.strftime("%H:%M")


def day_label(day: date, today: date, *, short: bool = False) -> str:
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tmrw" if short else "Tomorrow"
    return day.strftime("%a %d" if short else "%a %d %b")


def age_text(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h {minutes % 60} min ago"


def provenance(result: FetchResult) -> str:
    """One honest sentence about where the numbers came from."""
    report = result.report
    zone = report.timezone_abbreviation or ""
    stamp = f"{report.current.time:%Y-%m-%d %H:%M} {zone} ({report.timezone})".replace("  ", " ")
    if result.origin is Origin.DEMO:
        return f"Synthetic sample for offline demo - NOT live weather - sample time {stamp}"
    age = age_text(result.as_of - report.fetched_at)
    if result.origin is Origin.STALE:
        return (
            f"Provider unreachable - showing cached data fetched {age} - conditions as of {stamp}"
        )
    if result.origin is Origin.CACHE:
        return f"{report.provider} model data, cached (fetched {age}) - conditions as of {stamp}"
    return f"{report.provider} model data, conditions as of {stamp}"


def location_line(result: FetchResult) -> str:
    location = result.report.location
    label = location.label
    if location.postcode:
        label += f" - postal code {location.postcode}"
    return label


def coordinates(result: FetchResult) -> str:
    loc = result.report.location
    lat = f"{abs(loc.latitude):.2f}{'N' if loc.latitude >= 0 else 'S'}"
    lon = f"{abs(loc.longitude):.2f}{'E' if loc.longitude >= 0 else 'W'}"
    parts = [f"{lat} {lon}"]
    if loc.elevation_m is not None:
        parts.append(f"elev. {loc.elevation_m:.0f} m")
    parts.append(result.report.timezone)
    return " - ".join(parts)


# --------------------------------------------------------------------------- Rich


def _temp_style(celsius: float | None) -> str:
    if celsius is None:
        return "default"
    if celsius <= 0:
        return "bold #7fb2ff"
    if celsius <= 10:
        return "bold #4fc3f7"
    if celsius <= 20:
        return "bold #3ddc84"
    if celsius <= 28:
        return "bold #ffb000"
    return "bold #ff6b4a"


def render_rich(result: FetchResult, view: ViewOptions, width: int) -> RenderableType:
    fmt = Formatter(view.units, view.ascii_only)
    parts: list[RenderableType] = [_hero(result, fmt, width)]
    hours = upcoming_hours(result.report.hourly, result.as_of, view.hour_count)
    if hours:
        parts.append(_hourly_panel(hours, fmt, width))
    if view.forecast:
        days = upcoming_days(result.report, result.as_of)
        if days:
            parts.append(_daily_panel(days, local_today(result.report, result.as_of), fmt, width))
    parts.append(_footer(result, view))
    return Group(*parts)


def _badge(origin: Origin) -> Text:
    label, style = _ORIGIN_BADGE[origin]
    return Text(f" {label} ", style=style)


def _hero(result: FetchResult, fmt: Formatter, width: int) -> Panel:
    report, current = result.report, result.report.current
    condition = describe(current.weather_code)
    today = today_forecast(report, result.as_of)

    headline = Text()
    headline.append(fmt.temp(current.temperature_c), style=_temp_style(current.temperature_c))
    headline.append("\n")
    headline.append(f"{fmt.icon(current.weather_code, current.is_day)}  {condition.description}")
    headline.append("\n")
    headline.append("Feels like ", style="label")
    headline.append(fmt.temp(current.apparent_temperature_c))
    if today is not None:
        headline.append("\n")
        headline.append("Today ", style="label")
        headline.append(f"{fmt.temp(today.temperature_min_c, unit=False)} to ")
        headline.append(fmt.temp(today.temperature_max_c))

    facts = [
        ("Humidity", fmt.pct(current.relative_humidity_pct)),
        ("Wind", fmt.wind(current)),
        ("Gusts", fmt.speed(current.wind_gusts_kmh)),
        ("Pressure", fmt.pressure(current.pressure_msl_hpa)),
        ("Clouds", fmt.pct(current.cloud_cover_pct)),
        ("Precip.", fmt.precip(current.precipitation_mm)),
    ]
    if today is not None and (today.sunrise or today.sunset):
        facts.append(("Sun", f"{clock(today.sunrise)} - {clock(today.sunset)}"))

    art = Text("\n").join(
        Text.from_markup(line) for line in illustration(condition.kind, current.is_day)
    )
    wide = width >= 104
    top = Table.grid(padding=(0, 3))
    top_cells: list[RenderableType] = [art] if width >= 50 else []
    top_cells.append(headline)
    if wide:
        top_cells.append(_facts_grid(facts, pairs_per_row=1))
    for _ in top_cells:
        top.add_column(vertical="middle")
    top.add_row(*top_cells)

    body: list[RenderableType] = [top]
    if not wide:
        body += [Text(""), _facts_grid(facts, pairs_per_row=2 if width >= 72 else 1)]
    body += [Text(""), Text(f"{provenance(result)}\n{coordinates(result)}", style="meta")]
    title = Text.assemble(
        ("" if fmt.ascii_only else "\U0001f4cd "), (location_line(result), "bold"), "  ",
        _badge(result.origin),
    )  # fmt: skip
    border = {Origin.DEMO: "#ffb000", Origin.STALE: "#ff9e3d"}.get(result.origin, "#4fc3f7")
    return Panel(
        Group(*body),
        title=title,
        title_align="left",
        border_style=border,
        box=box.ASCII if fmt.ascii_only else box.ROUNDED,
        padding=(1, 2),
    )


def _facts_grid(facts: Sequence[tuple[str, str]], pairs_per_row: int) -> Table:
    grid = Table.grid(padding=(0, 2))
    for _ in range(pairs_per_row):
        grid.add_column(style="label", no_wrap=True)
        grid.add_column(no_wrap=True)
    rows = (len(facts) + pairs_per_row - 1) // pairs_per_row
    for row in range(rows):
        cells: list[str] = []
        for column in range(pairs_per_row):
            index = row + column * rows  # fill column-wise, like a newspaper
            cells.extend(facts[index] if index < len(facts) else ("", ""))
        grid.add_row(*cells)
    return grid


def _hourly_panel(hours: Sequence[HourlyPoint], fmt: Formatter, width: int) -> Panel:
    temps = [fmt.temp_value(h.temperature_c) for h in hours]
    rain = [h.precipitation_probability_pct for h in hours]
    cell = 2 if len(hours) * 2 + 14 <= width - 8 else 1
    rows = charts.column_chart(temps, height=5, ascii_only=fmt.ascii_only)
    present = [t for t in temps if t is not None]
    high, low = (max(present), min(present)) if present else (None, None)

    chart = Text()
    for index, row in enumerate(rows):
        label = ""
        if index == 0 and high is not None:
            label = f"{high:.1f}"
        elif index == len(rows) - 1 and low is not None:
            label = f"{low:.1f}"
        chart.append(f"{label:>6} ", style="label")
        stretched = "".join(c * cell for c in row)
        chart.append(stretched, style="#ffb000" if index < 2 else "#4fc3f7")
        chart.append("\n")
    chart.append(f"{fmt.temp_unit.strip():>6} ", style="label")
    chart.append(_hour_axis(hours, cell), style="label")
    if any(r is not None for r in rain):
        chart.append("\n")
        chart.append(f"{'rain':>6} ", style="label")
        spark = charts.sparkline(rain, ascii_only=fmt.ascii_only, span=(0.0, 100.0))
        chart.append("".join(c * cell for c in spark), style="rain")
        peak = max((r for r in rain if r is not None), default=0)
        chart.append(f"  max {peak:.0f}%", style="label")

    step = 1 if len(hours) <= 12 else 3
    table = Table(
        box=box.SIMPLE_HEAD if not fmt.ascii_only else box.ASCII2, expand=False, pad_edge=False
    )
    table.add_column("Time", style="label", no_wrap=True)
    table.add_column("Conditions", no_wrap=True)
    table.add_column(f"Temp {fmt.temp_unit.strip()}", justify="right", no_wrap=True, min_width=5)
    if width >= 70:
        table.add_column("Feels", justify="right", no_wrap=True)
    table.add_column("Rain", justify="right", no_wrap=True)
    if width >= 80:
        table.add_column("Wind", justify="right", no_wrap=True)
    for index, point in enumerate(hours[::step]):
        condition = describe(point.weather_code)
        cells: list[RenderableType] = [
            "Now" if index == 0 else clock(point.time),
            f"{fmt.icon(point.weather_code, point.is_day)} {condition.description}",
            Text(fmt.temp(point.temperature_c, unit=False), style=_temp_style(point.temperature_c)),
        ]
        if width >= 70:
            cells.append(fmt.temp(point.apparent_temperature_c, unit=False))
        cells.append(fmt.pct(point.precipitation_probability_pct))
        if width >= 80:
            cells.append(fmt.speed(point.wind_speed_kmh))
        table.add_row(*cells)

    title = f"Next {len(hours)} hours" + (" (every 3rd hour in table)" if step == 3 else "")
    return Panel(
        Group(chart, table),
        title=Text(title, style="accent"),
        title_align="left",
        border_style="#3a4a5c",
        box=box.ASCII if fmt.ascii_only else box.ROUNDED,
    )


def _hour_axis(hours: Sequence[HourlyPoint], cell: int) -> str:
    """Hour labels under the chart, placed every few columns without overlap."""
    axis = [" "] * (len(hours) * cell)
    every = max(1, 6 // cell)
    for index in range(0, len(hours), every):
        label = hours[index].time.strftime("%H")
        position = index * cell
        if position + len(label) <= len(axis):
            axis[position : position + len(label)] = list(label)
    return "".join(axis)


def _daily_panel(days: Sequence[DayForecast], today: date, fmt: Formatter, width: int) -> Panel:
    lows = [fmt.temp_value(d.temperature_min_c) or 0.0 for d in days]
    highs = [fmt.temp_value(d.temperature_max_c) or 0.0 for d in days]
    span_low, span_high = min(lows), max(highs)
    bar_width = 16 if width >= 100 else 10 if width >= 76 else 0

    table = Table(
        box=box.SIMPLE_HEAD if not fmt.ascii_only else box.ASCII2, expand=False, pad_edge=False
    )
    table.add_column("Day", style="bold", no_wrap=True)
    table.add_column("Conditions", no_wrap=True, overflow="ellipsis")
    table.add_column("Low", justify="right", no_wrap=True, min_width=5)
    if bar_width:
        table.add_column("", no_wrap=True)
    table.add_column("High", justify="right", no_wrap=True, min_width=5)
    table.add_column("Rain", justify="right", no_wrap=True, min_width=4)
    if width >= 92:
        table.add_column("Precip.", justify="right", no_wrap=True)
    if width >= 106:
        table.add_column("Wind max", justify="right", no_wrap=True)

    for day, low, high in zip(days, lows, highs, strict=True):
        condition = describe(day.weather_code)
        cells: list[RenderableType] = [
            day_label(day.date, today, short=width < 72),
            f"{fmt.icon(day.weather_code)} {condition.description}",
            Text(
                fmt.temp(day.temperature_min_c, unit=False),
                style=_temp_style(day.temperature_min_c),
            ),
        ]
        if bar_width:
            bar = charts.range_bar(
                low, high, span_low, span_high, bar_width, ascii_only=fmt.ascii_only
            )
            cells.append(_gradient_bar(bar))
        cells.append(
            Text(
                fmt.temp(day.temperature_max_c, unit=False),
                style=_temp_style(day.temperature_max_c),
            )
        )
        cells.append(fmt.pct(day.precipitation_probability_max_pct))
        if width >= 92:
            cells.append(fmt.precip(day.precipitation_sum_mm))
        if width >= 106:
            cells.append(fmt.speed(day.wind_speed_max_kmh))
        table.add_row(*cells)

    note = Text("Lows/highs cover each whole local calendar day.", style="meta")
    return Panel(
        Group(table, note),
        title=Text(f"{len(days)}-day forecast ({fmt.temp_unit.strip()})", style="accent"),
        title_align="left",
        border_style="#3a4a5c",
        box=box.ASCII if fmt.ascii_only else box.ROUNDED,
    )


def _gradient_bar(bar: str) -> Text:
    text = Text()
    filled = [i for i, c in enumerate(bar) if c not in " ·"]
    for index, char in enumerate(bar):
        if char in " ·":
            text.append(char, style="#3a4a5c")
            continue
        relative = (index - filled[0]) / max(1, len(filled) - 1)
        style = "#4fc3f7" if relative < 0.34 else "#3ddc84" if relative < 0.67 else "#ffb000"
        text.append(char, style=style)
    return text


def _footer(result: FetchResult, view: ViewOptions) -> Text:
    text = Text()
    for note in view.notes:
        text.append(f"Note: {note}\n", style="warn")
    if result.origin is Origin.STALE:
        text.append("Warning: the weather service could not be reached; data may be outdated.\n",
                    style="warn")  # fmt: skip
    if result.origin is Origin.DEMO:
        text.append("Offline demo: synthetic sample values, not a real forecast.\n", style="warn")
    text.append(ATTRIBUTION, style="meta")
    return text


# --------------------------------------------------------------------------- plain


def render_plain(result: FetchResult, view: ViewOptions) -> str:
    """ASCII-only text (place names are printed as the geocoder returned them)."""
    fmt = Formatter(view.units, ascii_only=True)
    report, current = result.report, result.report.current
    condition = describe(current.weather_code)
    badge = _ORIGIN_BADGE[result.origin][0]
    lines = [
        f"{location_line(result)}  [{badge}]",
        provenance(result),
        coordinates(result),
        "-" * 64,
    ]
    art = [
        Text.from_markup(line).plain.rstrip()
        for line in illustration(condition.kind, current.is_day)
    ]
    facts = [
        f"Temperature : {fmt.temp(current.temperature_c)}"
        f" (feels like {fmt.temp(current.apparent_temperature_c)})",
        f"Conditions  : {condition.description}",
        f"Humidity    : {fmt.pct(current.relative_humidity_pct)}",
        f"Wind        : {fmt.wind(current)} (gusts {fmt.speed(current.wind_gusts_kmh)})",
        f"Pressure    : {fmt.pressure(current.pressure_msl_hpa)}",
        f"Cloud cover : {fmt.pct(current.cloud_cover_pct)}",
        f"Precip.     : {fmt.precip(current.precipitation_mm)}",
    ]
    today = today_forecast(report, result.as_of)
    if today is not None:
        facts.append(f"Sun         : rise {clock(today.sunrise)}, set {clock(today.sunset)}")
    art_width = max((len(line) for line in art), default=0)
    for index in range(max(len(art), len(facts))):
        left = art[index] if index < len(art) else ""
        right = facts[index] if index < len(facts) else ""
        lines.append(f"{left:<{art_width}}  {right}".rstrip())

    hours = upcoming_hours(report.hourly, result.as_of, view.hour_count)
    if hours:
        lines += ["", f"Next {len(hours)} hours ({fmt.temp_unit.strip()})"]
        temps = [fmt.temp_value(h.temperature_c) for h in hours]
        lines.append("  trend  " + charts.sparkline(temps, ascii_only=True))
        lines.append(f"  {'Time':<6} {'Temp':>6} {'Feels':>6} {'Rain':>5}  Conditions")
        for index, point in enumerate(hours):
            lines.append(
                f"  {'Now' if index == 0 else clock(point.time):<6}"
                f" {fmt.temp(point.temperature_c, unit=False):>6}"
                f" {fmt.temp(point.apparent_temperature_c, unit=False):>6}"
                f" {fmt.pct(point.precipitation_probability_pct):>5}"
                f"  {describe(point.weather_code).description}"
            )

    if view.forecast:
        days = upcoming_days(report, result.as_of)
        if days:
            today_date = local_today(report, result.as_of)
            lows = [fmt.temp_value(d.temperature_min_c) or 0.0 for d in days]
            highs = [fmt.temp_value(d.temperature_max_c) or 0.0 for d in days]
            lines += ["", f"{len(days)}-day forecast ({fmt.temp_unit.strip()})"]
            lines.append(f"  {'Day':<11} {'Low':>6} {'':12} {'High':>6} {'Rain':>5}  Conditions")
            for day, low, high in zip(days, lows, highs, strict=True):
                bar = charts.range_bar(low, high, min(lows), max(highs), 10, ascii_only=True)
                lines.append(
                    f"  {day_label(day.date, today_date):<11}"
                    f" {fmt.temp(day.temperature_min_c, unit=False):>6} [{bar}]"
                    f" {fmt.temp(day.temperature_max_c, unit=False):>6}"
                    f" {fmt.pct(day.precipitation_probability_max_pct):>5}"
                    f"  {describe(day.weather_code).description}"
                )
    lines.append("")
    lines.extend(f"Note: {note}" for note in view.notes)
    if result.origin is Origin.STALE:
        lines.append("Warning: the weather service could not be reached; data may be outdated.")
    if result.origin is Origin.DEMO:
        lines.append("Offline demo: synthetic sample values, not a real forecast.")
    lines.append(ATTRIBUTION)
    return "\n".join(lines)


# --------------------------------------------------------------------------- JSON


def render_json(result: FetchResult, view: ViewOptions) -> str:
    fmt = Formatter(view.units, ascii_only=False)
    report, current = result.report, result.report.current
    location = report.location
    data: dict[str, Any] = {
        "schema": "think-weather-cli/report/v1",
        "mode": result.origin.value,
        "source": {
            "provider": report.provider,
            "attribution": ATTRIBUTION,
            "fetched_at": report.fetched_at.isoformat(),
            "age_seconds": max(0, round((result.as_of - report.fetched_at).total_seconds())),
            "synthetic": result.origin is Origin.DEMO,
        },
        "location": {
            "name": location.name,
            "admin1": location.admin1,
            "country": location.country,
            "country_code": location.country_code,
            "postcode": location.postcode,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "elevation_m": location.elevation_m,
            "timezone": report.timezone,
            "resolved_by": location.resolved_by,
        },
        "units": {
            "temperature": units.temperature_unit(view.units),
            "wind_speed": units.speed_unit(view.units),
            "precipitation": units.precipitation_unit(view.units),
            "pressure": "hPa",
            "humidity": "%",
        },
        "current": {
            "time": current.time.isoformat(),
            "temperature": fmt.temp_value(current.temperature_c),
            "feels_like": fmt.temp_value(current.apparent_temperature_c),
            "condition": describe(current.weather_code).description,
            "weather_code": current.weather_code,
            "is_day": current.is_day,
            "humidity_pct": _round(current.relative_humidity_pct),
            "wind_speed": fmt.speed_value(current.wind_speed_kmh),
            "wind_gusts": fmt.speed_value(current.wind_gusts_kmh),
            "wind_direction_deg": current.wind_direction_deg,
            "wind_direction": (
                units.compass(current.wind_direction_deg)
                if current.wind_direction_deg is not None
                else None
            ),
            "pressure_msl_hpa": current.pressure_msl_hpa,
            "cloud_cover_pct": _round(current.cloud_cover_pct),
            "precipitation": fmt.precip_value(current.precipitation_mm),
        },
    }
    hours = upcoming_hours(report.hourly, result.as_of, view.hour_count)
    if hours:
        data["hourly"] = [
            {
                "time": h.time.isoformat(),
                "temperature": fmt.temp_value(h.temperature_c),
                "feels_like": fmt.temp_value(h.apparent_temperature_c),
                "condition": describe(h.weather_code).description,
                "weather_code": h.weather_code,
                "precipitation_probability_pct": _round(h.precipitation_probability_pct),
                "humidity_pct": _round(h.relative_humidity_pct),
                "wind_speed": fmt.speed_value(h.wind_speed_kmh),
            }
            for h in hours
        ]
    if view.forecast:
        data["daily"] = [
            {
                "date": d.date.isoformat(),
                "condition": describe(d.weather_code).description,
                "weather_code": d.weather_code,
                "min": fmt.temp_value(d.temperature_min_c),
                "max": fmt.temp_value(d.temperature_max_c),
                "precipitation_probability_max_pct": _round(d.precipitation_probability_max_pct),
                "precipitation_sum": fmt.precip_value(d.precipitation_sum_mm),
                "wind_speed_max": fmt.speed_value(d.wind_speed_max_kmh),
                "sunrise": d.sunrise.isoformat() if d.sunrise else None,
                "sunset": d.sunset.isoformat() if d.sunset else None,
            }
            for d in upcoming_days(report, result.as_of)
        ]
    if view.notes:
        data["notes"] = list(view.notes)
    return json.dumps(data, ensure_ascii=True, indent=2)


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)
