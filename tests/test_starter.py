"""Regression tests carried over from the original offline starter."""

from __future__ import annotations

import json

import pytest

from weather_cli.cli import main
from weather_cli.units import temperature, wind


def test_metric_imperial() -> None:
    assert temperature(0, "fahrenheit") == 32
    assert temperature(100, "fahrenheit") == 212
    assert abs(wind(1.609344, "fahrenheit") - 1) < 1e-10


def test_demo_json_is_valid(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["Yerevan", "--demo", "--forecast", "--hours", "12", "--output", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "offline-synthetic-sample"
    assert len(output["daily"]) == 5
    assert len(output["hourly"]) == 12
    assert output["current"]["humidity_pct"] == 52


@pytest.mark.expects_blocked_network
def test_no_fake_live_fallback(capsys, network_guard) -> None:  # type: ignore[no-untyped-def]
    # The starter asserted "live is not implemented". Live mode now exists, so the
    # invariant becomes: when the network is unavailable, live mode fails with the
    # provider exit code and never silently substitutes the sample fixture.
    assert main(["Yerevan", "--no-cache"]) == 5
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not connect" in captured.err.lower()
    assert network_guard  # a real connection was attempted and refused


def test_bad_demo_city(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["London", "--demo"]) == 3
    assert "only contains" in capsys.readouterr().err
