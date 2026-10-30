"""Render real program output to SVG screenshots for the README.

    python scripts/capture_screenshots.py            # offline demo only
    python scripts/capture_screenshots.py --live     # also a live Yerevan capture

The SVGs are produced by Rich's own recorder from the exact renderables the CLI
prints; nothing is drawn by hand. Live captures show real data at capture time.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path

from rich.console import Console

from weather_cli.cli import build_parser, build_service
from weather_cli.locations import parse_query
from weather_cli.models import UnitSystem
from weather_cli.rendering import THEME, ViewOptions, render_rich

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def capture(argv: list[str], view: ViewOptions, name: str, title: str, width: int = 100) -> None:
    args = build_parser().parse_args(argv)
    with ExitStack() as stack:
        service = build_service(args, stack)
        resolution = service.resolve(parse_query(args.location, args.country))
        assert resolution.location is not None
        result = service.fetch(resolution.location)
    console = Console(record=True, width=width, theme=THEME, force_terminal=True, emoji=False)
    console.print(render_rich(result, view, width))
    OUT.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(OUT / f"{name}.svg"), title=title)
    print(f"wrote docs/screenshots/{name}.svg")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    live = parser.parse_args().live
    capture(
        ["Yerevan", "--demo"],
        ViewOptions(forecast=True, hours=12),
        "demo-forecast",
        "weather Yerevan --demo --forecast --hours 12",
    )
    if live:
        capture(
            ["Yerevan", "--country", "AM", "--no-cache"],
            ViewOptions(forecast=True),
            "live-yerevan",
            "weather Yerevan --country AM --forecast",
        )
        capture(
            ["90210", "--country", "US", "--no-cache"],
            ViewOptions(units=UnitSystem.IMPERIAL, hours=12),
            "live-90210-imperial",
            "weather 90210 --country US --units imperial --hours 12",
        )


if __name__ == "__main__":
    main()
