"""User-facing error taxonomy and process exit codes.

Every *expected* operational failure (bad input, unknown place, network trouble)
is a :class:`WeatherError` carrying a short message, an actionable hint and an
exit code. Anything else is a programming error and is reported as internal.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weather_cli.models import Location


class ExitCode(IntEnum):
    OK = 0
    INTERNAL = 1
    USAGE = 2
    NOT_FOUND = 3
    AMBIGUOUS = 4
    PROVIDER = 5
    RATE_LIMITED = 6
    INTERRUPTED = 130


class WeatherError(Exception):
    """Base class for failures that should be explained to the user, not traced."""

    exit_code: ExitCode = ExitCode.INTERNAL
    code: str = "error"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class InputError(WeatherError):
    """The user's input is invalid before any network request is made."""

    exit_code = ExitCode.USAGE
    code = "invalid_input"


class LocationNotFoundError(WeatherError):
    exit_code = ExitCode.NOT_FOUND
    code = "location_not_found"


class AmbiguousLocationError(WeatherError):
    """Several distinct places match and the program may not ask the user."""

    exit_code = ExitCode.AMBIGUOUS
    code = "ambiguous_location"

    def __init__(self, message: str, candidates: Sequence[Location], hint: str | None = None):
        super().__init__(message, hint)
        self.candidates = tuple(candidates)


class ProviderError(WeatherError):
    """The remote service is unreachable, failing, or returned unusable data."""

    exit_code = ExitCode.PROVIDER
    code = "provider_unavailable"

    def __init__(self, message: str, hint: str | None = None, *, transient: bool = False):
        super().__init__(message, hint)
        self.transient = transient
        """True for outages (network, timeout, 5xx) where showing stale data is fair."""


class RateLimitedError(ProviderError):
    exit_code = ExitCode.RATE_LIMITED
    code = "rate_limited"
