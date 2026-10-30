"""A simple prompt-and-menu console used when ``weather`` runs without a location.

Deliberately not a full-screen TUI: plain prompts work in every terminal, are
easy to test with scripted answers, and never trap the user.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from rich.console import Console
from rich.text import Text

from weather_cli.errors import WeatherError
from weather_cli.locations import looks_like_postcode, normalize_country, parse_query
from weather_cli.models import FetchResult, Location, UnitSystem
from weather_cli.rendering import ViewOptions, render_rich
from weather_cli.service import WeatherService

Ask = Callable[[str], str]
"""Reads one line of input; raises EOFError/KeyboardInterrupt to end the session."""

ErrorReporter = Callable[[WeatherError], None]


class _Cancelled(Exception):
    pass


class InteractiveSession:
    def __init__(
        self,
        service: WeatherService,
        console: Console,
        ask: Ask,
        report_error: ErrorReporter,
        view: ViewOptions,
        country: str | None = None,
    ) -> None:
        self.service = service
        self.console = console
        self.ask = ask
        self.report_error = report_error
        self.view = view
        self.default_country = country
        self.result: FetchResult | None = None
        self.location: Location | None = None
        self.notes: tuple[str, ...] = ()

    def run(self) -> int:
        self.console.print(
            Text("Think Weather", style="accent")
            + Text("  - type a city or postal code; Ctrl+C to quit", style="meta")
        )
        try:
            self._ask_units()
            while True:
                if self.result is None:
                    self._search()
                    continue
                if not self._menu():
                    break
        except (EOFError, KeyboardInterrupt):
            self.console.print()
        self.console.print(Text("Goodbye!", style="meta"))
        return 0

    # -- steps ---------------------------------------------------------------------

    def _ask_units(self) -> None:
        default = "F" if self.view.units is UnitSystem.IMPERIAL else "C"
        while True:
            answer = self.ask(f"Units: Celsius or Fahrenheit? [C/F] (default {default}): ")
            if not answer.strip():
                return
            try:
                self.view = replace(self.view, units=UnitSystem.parse(answer))
                return
            except ValueError:
                self.console.print("Please type C or F.", style="warn")

    def _search(self) -> None:
        text = self.ask("City or postal code (q to quit): ").strip()
        if text.casefold() in {"q", "quit", "exit"}:
            raise EOFError
        if not text:
            return
        try:
            country = self.default_country
            if looks_like_postcode(text) and not country:
                answer = self.ask("Country code for this postal code (e.g. US, DE, AM): ")
                country = normalize_country(answer)
            query = parse_query(text, country)
            resolution = self.service.resolve(query, choose=self._choose)
            assert resolution.location is not None
            self.location = resolution.location
            self.notes = (resolution.note,) if resolution.note else ()
            self._fetch(refresh=False)
        except _Cancelled:
            return
        except WeatherError as error:
            self.report_error(error)

    def _fetch(self, *, refresh: bool) -> None:
        assert self.location is not None
        self.result = self.service.fetch(self.location, refresh=refresh)
        self._show()

    def _show(self) -> None:
        assert self.result is not None
        view = replace(self.view, notes=self.notes)
        self.console.print(render_rich(self.result, view, self.console.width))

    def _choose(self, candidates: Sequence[Location]) -> Location:
        self.console.print(Text("Several places match - which one did you mean?", style="accent"))
        for number, candidate in enumerate(candidates, start=1):
            self.console.print(Text(f"  {number:>2}. {candidate.detail}"))  # never markup
        while True:
            answer = self.ask(f"Choose 1-{len(candidates)} (Enter to cancel): ").strip()
            if not answer:
                raise _Cancelled
            if answer.isdigit() and 1 <= int(answer) <= len(candidates):
                return candidates[int(answer) - 1]
            self.console.print("Please enter one of the numbers shown.", style="warn")

    def _menu(self) -> bool:
        """One menu round. Returns False to quit."""
        other = "°F" if self.view.units is UnitSystem.METRIC else "°C"
        forecast = "hide" if self.view.forecast else "show"
        self.console.print(
            Text(
                f"[n] new search   [u] switch to {other}   [f] {forecast} forecast"
                "   [r] refresh   [q] quit",
                style="meta",
            )
        )
        choice = self.ask("> ").strip().casefold()
        if choice in {"q", "quit", "exit"}:
            return False
        if choice in {"n", "s", "new", "search"}:
            self.result = None
        elif choice in {"u", "units"}:
            self.view = replace(self.view, units=self.view.units.toggled())
            self._show()  # same data, re-rendered: no network request
        elif choice in {"f", "forecast"}:
            self.view = replace(self.view, forecast=not self.view.forecast)
            self._show()
        elif choice in {"r", "refresh"}:
            try:
                self._fetch(refresh=True)
            except WeatherError as error:
                self.report_error(error)
        elif choice:
            self.console.print("Unknown option.", style="warn")
        return True
