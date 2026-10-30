"""A small JSON-over-HTTPS client: explicit timeouts, bounded retries, clear errors.

Only GET is used. Transient failures (timeouts, connection errors, 429, 5xx)
are retried at most ``max_retries`` times with capped exponential backoff and
jitter. A server asking us to wait longer than ``retry_after_cap`` seconds is not
waited for; the user gets a "try again later" message instead of a frozen
terminal. Logs contain host and path only, never query strings.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from weather_cli.config import USER_AGENT
from weather_cli.errors import ProviderError, RateLimitedError

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

QueryParams = Mapping[str, str | int | float]


class HttpClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        backoff_cap: float = 4.0,
        retry_after_cap: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._retry_after_cap = retry_after_cap
        self._sleep = sleep
        self._jitter = jitter
        self.request_count = 0

    def close(self) -> None:
        self._client.close()

    def get_json(
        self,
        url: str,
        params: QueryParams | None = None,
        *,
        service: str,
        not_found_ok: bool = False,
    ) -> Any:
        """GET ``url`` and decode JSON. Returns ``None`` for 404 when ``not_found_ok``."""
        attempt = 0
        while True:
            attempt += 1
            response, failure = self._attempt(url, params, service, attempt)
            if response is not None and response.status_code not in RETRYABLE_STATUS:
                return self._decode(response, service, not_found_ok)

            delay = self._retry_delay(response, attempt)
            if attempt > self._max_retries or delay is None:
                raise self._final_error(response, failure, service)
            log.debug("%s: retrying in %.2fs (attempt %d failed)", service, delay, attempt)
            self._sleep(delay)

    def _attempt(
        self, url: str, params: QueryParams | None, service: str, attempt: int
    ) -> tuple[httpx.Response | None, ProviderError | None]:
        request = self._client.build_request("GET", url, params=params)
        where = f"{request.url.host}{request.url.path}"
        started = time.perf_counter()
        self.request_count += 1
        try:
            response = self._client.send(request)
        except httpx.TimeoutException:
            log.debug("GET %s timed out (attempt %d)", where, attempt)
            return None, ProviderError(
                f"{service} did not respond in time.",
                "Check your internet connection and try again in a moment.",
                transient=True,
            )
        except httpx.TransportError as exc:
            log.debug("GET %s failed: %s (attempt %d)", where, type(exc).__name__, attempt)
            return None, ProviderError(
                f"Could not connect to {service}.",
                "Check your internet connection, VPN or proxy, or try "
                "'weather Yerevan --demo' for offline sample data.",
                transient=True,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.debug(
            "GET %s -> HTTP %d in %.0f ms (attempt %d)",
            where,
            response.status_code,
            elapsed_ms,
            attempt,
        )
        return response, None

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float | None:
        """Seconds to wait before the next attempt, or ``None`` to stop retrying."""
        if response is not None:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                return retry_after if retry_after <= self._retry_after_cap else None
        backoff: float = min(self._backoff_cap, self._backoff_base * 2.0 ** (attempt - 1))
        return backoff * (0.5 + self._jitter() / 2)

    def _final_error(
        self, response: httpx.Response | None, failure: ProviderError | None, service: str
    ) -> ProviderError:
        if response is None:
            assert failure is not None
            return failure
        wait = _parse_retry_after(response.headers.get("Retry-After"))
        later = f"in about {int(wait)} seconds" if wait else "in a minute"
        if response.status_code == 429:
            return RateLimitedError(
                f"{service} is rate limiting requests (HTTP 429).",
                f"Wait and try again {later}; cached results are reused for 10 minutes.",
            )
        return ProviderError(
            f"{service} is temporarily unavailable (HTTP {response.status_code}).",
            f"This is a problem on the provider's side. Try again {later}.",
            transient=True,
        )

    def _decode(self, response: httpx.Response, service: str, not_found_ok: bool) -> Any:
        status = response.status_code
        if status == 404 and not_found_ok:
            return None
        payload: Any = None
        try:
            payload = response.json()
        except ValueError:
            if status < 400:
                raise ProviderError(
                    f"{service} returned a response that is not valid JSON.",
                    "The service may be having problems; try again later.",
                ) from None
        if status >= 400:
            reason = payload.get("reason") if isinstance(payload, dict) else None
            detail = f": {reason}" if isinstance(reason, str) else ""
            raise ProviderError(
                f"{service} rejected the request (HTTP {status}){detail}.",
                "If this keeps happening, please report it with the --verbose output.",
            )
        if isinstance(payload, dict) and payload.get("error") is True:
            reason = payload.get("reason")
            raise ProviderError(
                f"{service} reported an error: {reason if isinstance(reason, str) else 'unknown'}.",
                "If this keeps happening, please report it with the --verbose output.",
            )
        return payload


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to our own backoff.
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)
