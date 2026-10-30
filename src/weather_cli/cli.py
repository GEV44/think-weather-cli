"""Command-line entry point: argument parsing, wiring, output and exit codes.

The CLI never performs HTTP itself and never maps provider JSON; it builds a
:class:`WeatherService`, asks it for a :class:`FetchResult` and hands that to a
renderer. One-shot invocations never read stdin.
"""

from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from typing import TextIO

from rich.console import Console

from weather_cli import __version__
from weather_cli.cache import FILE_NAME, SQLiteCache
from weather_cli.config import ATTRIBUTION, MAX_HOURS, STALE_GRACE, cache_dir
from weather_cli.errors import AmbiguousLocationError, ExitCode, InputError, WeatherError
from weather_cli.locations import parse_query
from weather_cli.models import UnitSystem
from weather_cli.rendering import THEME, ViewOptions, render_json, render_plain, render_rich
from weather_cli.service import CacheMode, WeatherService

log = logging.getLogger("weather_cli")

EPILOG = """\
examples:
  weather Yerevan --country AM            current weather
  weather "New York" --units imperial     Fahrenheit and mph
  weather 90210 --country US --forecast   US ZIP code + 5-day forecast
  weather Yerevan --hours 12              next 12 hours with a chart
  weather Yerevan --output json           machine-readable JSON
  weather Yerevan --demo --forecast       offline synthetic sample (no network)
  weather                                 interactive mode (in a terminal)
  weather cache stats | clear | path      inspect or empty the local cache

Postal codes need --country because codes repeat across countries.
exit codes: 0 ok, 1 internal error, 2 invalid input, 3 location not found,
            4 ambiguous location, 5 weather service unavailable, 6 rate limited
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="weather",
        description="Current weather and forecasts for a city or postal code, in your terminal.",
        epilog=EPILOG + f"\n{ATTRIBUTION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "location",
        nargs="?",
        help='city name ("City, Region" to narrow down) or postal code; omit for interactive mode',
    )
    parser.add_argument(
        "-c", "--country", metavar="CC", help="two-letter country code, e.g. AM, US, DE"
    )
    parser.add_argument(
        "-u",
        "--units",
        default="metric",
        metavar="UNITS",
        help="metric/celsius/c (default) or imperial/fahrenheit/f",
    )
    parser.add_argument(
        "-f", "--forecast", action="store_true", help="add the 5-day and next-24-hours forecast"
    )
    parser.add_argument(
        "-H",
        "--hours",
        type=int,
        metavar="N",
        help=f"show the next N hours (1-{MAX_HOURS}) as a chart and table",
    )
    parser.add_argument(
        "-o", "--output", choices=["text", "json"], default="text", help="output format"
    )
    parser.add_argument(
        "--plain", action="store_true", help="plain ASCII text: no colors, emoji or box drawing"
    )
    parser.add_argument("--no-color", action="store_true", help="disable colors (or set NO_COLOR)")
    parser.add_argument(
        "--demo", action="store_true", help="offline synthetic Yerevan sample; no network used"
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--refresh", action="store_true", help="ignore cached weather, fetch fresh data"
    )
    cache_group.add_argument(
        "--no-cache", action="store_true", help="neither read nor write the local cache"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="diagnostics (requests, cache) on stderr"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


# --------------------------------------------------------------------------- wiring


def _cache() -> SQLiteCache:
    return SQLiteCache(cache_dir() / FILE_NAME, retention=STALE_GRACE)


def build_live_service(args: argparse.Namespace, stack: ExitStack) -> WeatherService:
    from weather_cli.http import HttpClient
    from weather_cli.providers.open_meteo import OpenMeteoForecaster, OpenMeteoGeocoder
    from weather_cli.providers.postal import ZippopotamPostalLookup

    http = HttpClient()
    stack.callback(http.close)
    mode = (
        CacheMode.OFF if args.no_cache else CacheMode.REFRESH if args.refresh else CacheMode.NORMAL
    )
    return WeatherService(
        OpenMeteoGeocoder(http),
        OpenMeteoForecaster(http),
        postal=ZippopotamPostalLookup(http),
        cache=None if mode is CacheMode.OFF else _cache(),
        cache_mode=mode,
    )


def build_service(args: argparse.Namespace, stack: ExitStack) -> WeatherService:
    if args.demo:
        from weather_cli.demo import build_demo_service

        return build_demo_service()
    return build_live_service(args, stack)


def _configure_logging(verbose: bool, stream: TextIO) -> None:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if verbose else logging.WARNING)
    log.propagate = False


def _supports_unicode(console: Console) -> bool:
    return console.encoding.lower().replace("-", "").startswith("utf")


def _make_console(args: argparse.Namespace, stream: TextIO) -> Console:
    no_color = args.no_color or args.plain or "NO_COLOR" in os.environ
    return Console(
        file=stream,
        theme=THEME,
        no_color=no_color,
        highlight=False,
        emoji=False,
        soft_wrap=False,
    )


# --------------------------------------------------------------------------- errors


class OutputClosedError(Exception):
    """The reader of stdout went away (e.g. ``weather ... | head``)."""


def _print_error(error: WeatherError, args: argparse.Namespace | None, stream: TextIO) -> None:
    if args is not None and args.output == "json":
        payload: dict[str, object] = {
            "error": {
                "code": error.code,
                "message": error.message,
                "hint": error.hint,
                "exit_code": int(error.exit_code),
            }
        }
        if isinstance(error, AmbiguousLocationError):
            payload["error"] = {
                **payload["error"],  # type: ignore[dict-item]
                "candidates": [c.detail for c in error.candidates],
            }
        stream.write(json.dumps(payload, ensure_ascii=True) + "\n")
        return
    lines = [f"error: {error.message}"]
    if isinstance(error, AmbiguousLocationError):
        lines += [f"  {n}. {c.detail}" for n, c in enumerate(error.candidates, start=1)]
    if error.hint:
        lines.append(f"hint: {error.hint}")
    stream.write("\n".join(lines) + "\n")
    stream.flush()


# --------------------------------------------------------------------------- commands


def _validate(args: argparse.Namespace) -> tuple[UnitSystem, int | None]:
    try:
        system = UnitSystem.parse(args.units)
    except ValueError:
        raise InputError(
            f"Unknown units '{args.units}'.", "Use metric (Celsius, km/h) or imperial (°F, mph)."
        ) from None
    if args.hours is not None and not 1 <= args.hours <= MAX_HOURS:
        raise InputError(
            f"--hours must be between 1 and {MAX_HOURS} (got {args.hours}).",
            "Example: weather Yerevan --hours 12",
        )
    return system, args.hours


def run_once(args: argparse.Namespace, out: TextIO) -> int:
    system, hours = _validate(args)
    location_text = args.location
    if location_text is None and args.demo:
        from weather_cli.demo import DEFAULT_DEMO_LOCATION

        location_text = DEFAULT_DEMO_LOCATION
    query = parse_query(location_text, args.country)
    with ExitStack() as stack:
        service = build_service(args, stack)
        resolution = service.resolve(query)
        assert resolution.location is not None
        result = service.fetch(resolution.location)
    view = ViewOptions(
        units=system,
        forecast=args.forecast,
        hours=hours,
        notes=(resolution.note,) if resolution.note else (),
    )
    try:
        if args.output == "json":
            out.write(render_json(result, view) + "\n")
        elif args.plain:
            out.write(render_plain(result, view) + "\n")
        else:
            console = _make_console(args, out)
            ascii_only = not _supports_unicode(console)
            view = ViewOptions(view.units, view.forecast, view.hours, view.notes, ascii_only)
            console.print(render_rich(result, view, console.width))
    except OSError as exc:
        if exc.errno in (errno.EPIPE, errno.EINVAL):
            raise OutputClosedError from exc
        raise
    return ExitCode.OK


def run_interactive(
    args: argparse.Namespace, out: TextIO, err: TextIO, ask: Callable[[str], str]
) -> int:
    from weather_cli.interactive import InteractiveSession

    system, hours = _validate(args)
    console = _make_console(args, out)
    view = ViewOptions(
        units=system,
        forecast=args.forecast,
        hours=hours,
        ascii_only=args.plain or not _supports_unicode(console),
    )
    with ExitStack() as stack:
        service = build_service(args, stack)
        session = InteractiveSession(
            service,
            console,
            ask,
            lambda error: _print_error(error, None, err),
            view,
            country=args.country.upper() if args.country else None,
        )
        return session.run()


def run_cache_command(argv: Sequence[str], out: TextIO, err: TextIO) -> int:
    parser = argparse.ArgumentParser(prog="weather cache", description="Manage the local cache.")
    parser.add_argument("action", choices=["stats", "clear", "path"])
    args = parser.parse_args(argv)
    cache = _cache()
    if args.action == "path":
        out.write(f"{cache.path}\n")
    elif args.action == "clear":
        removed = cache.clear()
        out.write(
            f"Removed {removed} cached entr{'y' if removed == 1 else 'ies'} ({cache.path}).\n"
        )
    else:
        stats = cache.stats(datetime.now(UTC))
        out.write(f"Cache file : {stats.path}\n")
        if not stats.available:
            out.write("Status     : empty (not created yet) or unavailable\n")
            return ExitCode.OK
        out.write(f"Size       : {stats.size_bytes / 1024:.1f} KiB\n")
        if not stats.entries:
            out.write("Entries    : none\n")
        for namespace, (fresh, expired) in stats.entries.items():
            out.write(f"{namespace:<28} {fresh:>4} fresh  {expired:>4} expired\n")
    return ExitCode.OK


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def _input(prompt: str) -> str:
    return input(prompt)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    ask: Callable[[str], str] | None = None,
) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="replace")  # never crash on a terminal that lacks a glyph

    args: argparse.Namespace | None = None
    try:
        if args_list[:1] == ["cache"] and (
            len(args_list) == 1 or args_list[1] in {"stats", "clear", "path", "-h", "--help"}
        ):
            _configure_logging(False, err)
            with redirect_stdout(out), redirect_stderr(err):  # argparse help/usage
                return run_cache_command(args_list[1:], out, err)
        with redirect_stdout(out), redirect_stderr(err):
            args = build_parser().parse_args(args_list)
        _configure_logging(args.verbose, err)
        if args.location is not None or args.demo:
            return run_once(args, out)
        if (not stdin.isatty() or not out.isatty()) and ask is None:
            raise InputError(
                "No location given and this is not an interactive terminal.",
                "Pass a location, e.g.: weather Yerevan --country AM  (see weather --help)",
            )
        return run_interactive(args, out, err, ask or _input)
    except SystemExit as exc:  # argparse --help/--version/usage errors
        return exc.code if isinstance(exc.code, int) else ExitCode.USAGE
    except WeatherError as error:
        _print_error(error, args, err)
        return error.exit_code
    except KeyboardInterrupt:
        err.write("\nInterrupted.\n")
        return ExitCode.INTERRUPTED
    except (OutputClosedError, BrokenPipeError):
        # The reader closed the pipe early: not an error. Avoid a second failure at exit.
        if out is sys.stdout:
            _silence_stdout()
        return ExitCode.OK
    except Exception:
        if args is not None and args.verbose:
            traceback.print_exc(file=err)
        err.write(
            "error: unexpected internal error (this is a bug).\n"
            "hint: re-run with --verbose to see details and please report it.\n"
        )
        return ExitCode.INTERNAL
