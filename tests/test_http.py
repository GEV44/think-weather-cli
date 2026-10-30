"""Retry, timeout and error-mapping policy of the HTTP client (no real sleeps)."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from weather_cli.errors import ExitCode, ProviderError, RateLimitedError
from weather_cli.http import HttpClient

URL = "https://api.example.test/data"


def client_for(
    responses: list[httpx.Response | Exception], sleeps: list[float] | None = None, **kwargs: float
) -> tuple[HttpClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    record: Callable[[float], None] = sleeps.append if sleeps is not None else lambda _: None
    client = HttpClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=record,
        jitter=lambda: 1.0,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, seen


def test_success_returns_json() -> None:
    client, seen = client_for([httpx.Response(200, json={"ok": 1})])
    assert client.get_json(URL, {"a": 1}, service="Test") == {"ok": 1}
    assert len(seen) == 1
    assert seen[0].url.params["a"] == "1"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_server_errors_are_retried_then_succeed(status: int) -> None:
    sleeps: list[float] = []
    client, seen = client_for(
        [httpx.Response(status), httpx.Response(200, json={"ok": True})], sleeps
    )
    assert client.get_json(URL, service="Test") == {"ok": True}
    assert len(seen) == 2
    assert len(sleeps) == 1


def test_retries_are_bounded_and_backoff_grows() -> None:
    sleeps: list[float] = []
    client, seen = client_for([httpx.Response(503)] * 3, sleeps)
    with pytest.raises(ProviderError, match="temporarily unavailable") as info:
        client.get_json(URL, service="Test")
    assert len(seen) == 3  # 1 attempt + 2 retries
    assert sleeps == [0.5, 1.0]
    assert info.value.transient
    assert info.value.exit_code is ExitCode.PROVIDER


def test_timeouts_are_retried_and_reported() -> None:
    timeout = httpx.ReadTimeout("slow")
    client, seen = client_for([timeout, timeout, timeout])
    with pytest.raises(ProviderError, match="did not respond in time") as info:
        client.get_json(URL, service="Open-Meteo forecast")
    assert len(seen) == 3
    assert info.value.transient


def test_dns_or_connection_failure_gives_actionable_message() -> None:
    error = httpx.ConnectError("Name or service not known")
    client, _ = client_for([error] * 3)
    with pytest.raises(ProviderError, match="Could not connect") as info:
        client.get_json(URL, service="Open-Meteo geocoding")
    assert info.value.hint is not None and "--demo" in info.value.hint


def test_rate_limit_respects_short_retry_after() -> None:
    sleeps: list[float] = []
    client, _seen = client_for(
        [httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json=[])], sleeps
    )
    assert client.get_json(URL, service="Test") == []
    assert sleeps == [2.0]


def test_rate_limit_with_huge_retry_after_fails_fast() -> None:
    sleeps: list[float] = []
    client, seen = client_for([httpx.Response(429, headers={"Retry-After": "3600"})], sleeps)
    with pytest.raises(RateLimitedError, match="rate limiting") as info:
        client.get_json(URL, service="Test")
    assert sleeps == []  # never sleeps for an hour
    assert len(seen) == 1
    assert info.value.exit_code is ExitCode.RATE_LIMITED
    assert info.value.hint is not None and "3600" in info.value.hint


def test_rate_limit_without_header_uses_backoff_then_fails() -> None:
    client, seen = client_for([httpx.Response(429)] * 3)
    with pytest.raises(RateLimitedError):
        client.get_json(URL, service="Test")
    assert len(seen) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(status: int) -> None:
    client, seen = client_for([httpx.Response(status, json={"reason": "bad"})])
    with pytest.raises(ProviderError, match=f"HTTP {status}"):
        client.get_json(URL, service="Test")
    assert len(seen) == 1


def test_404_can_mean_not_found() -> None:
    client, _ = client_for([httpx.Response(404, json={})])
    assert client.get_json(URL, service="Test", not_found_ok=True) is None


def test_invalid_json_is_a_provider_error() -> None:
    client, _ = client_for([httpx.Response(200, content=b"<html>oops</html>")])
    with pytest.raises(ProviderError, match="not valid JSON"):
        client.get_json(URL, service="Test")


def test_json_error_flag_with_200_is_reported() -> None:
    client, _ = client_for([httpx.Response(200, json={"error": True, "reason": "nope"})])
    with pytest.raises(ProviderError, match="nope"):
        client.get_json(URL, service="Test")


def test_http_date_retry_after_falls_back_to_backoff() -> None:
    sleeps: list[float] = []
    client, _ = client_for(
        [
            httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            httpx.Response(200, json={}),
        ],
        sleeps,
    )
    client.get_json(URL, service="Test")
    assert sleeps == [0.5]


def test_default_client_has_explicit_timeouts_and_user_agent() -> None:
    client = HttpClient()
    try:
        inner = client._client
        assert inner.timeout.connect == 3.0
        assert inner.timeout.read == 5.0
        assert inner.headers["User-Agent"].startswith("think-weather-cli/")
    finally:
        client.close()


def test_logs_never_include_query_strings(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = client_for([httpx.Response(200, json={})])
    with caplog.at_level("DEBUG", logger="weather_cli"):
        client.get_json(URL, {"name": "secret-place"}, service="Test")
    assert "api.example.test/data" in caplog.text
    assert "secret-place" not in caplog.text
