"""Shared retry utility for enrichment API calls.

Provides exponential backoff with jitter, per-track timeout caps,
header-aware waiting (for APIs with X-RateLimit-Reset), and
transient vs permanent error classification.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib.error import HTTPError, URLError

T = TypeVar("T")

# Transient HTTP codes (retryable)
_TRANSIENT_CODES: set[int] = {429, 500, 502, 503, 504}

# Permanent HTTP codes (never retry)
_PERMANENT_CODES: set[int] = {401, 403, 404}


class TransientAPIError(Exception):
    """Raised for retryable API errors (429, 5xx, timeouts)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: dict | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.headers = headers or {}


class PermanentAPIError(Exception):
    """Raised for non-retryable API errors (401, 403, 404)."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    per_track_timeout: float = 300.0  # 5 minutes
    jitter_factor: float = 0.25


@dataclass
class RetryStats:
    """Accumulates retry/wait statistics across a run."""

    retries: int = 0
    rate_limit_waits: int = 0
    total_wait_seconds: float = 0.0
    permanent_failures: int = 0
    transient_failures: int = 0

    def summary(self) -> str:
        """Format a summary line for end-of-run reporting."""
        parts = []
        if self.retries:
            parts.append(f"retries: {self.retries}")
        if self.rate_limit_waits:
            parts.append(f"rate limit waits: {self.rate_limit_waits}")
        if self.total_wait_seconds > 0.1:
            parts.append(f"total wait: {self.total_wait_seconds:.1f}s")
        if self.permanent_failures:
            parts.append(f"permanent failures: {self.permanent_failures}")
        if self.transient_failures:
            parts.append(f"failed after retries: {self.transient_failures}")
        return " | ".join(parts) if parts else "no issues"


def is_transient(exc: Exception) -> bool:
    """Determine if an error is transient (retryable)."""
    if isinstance(exc, TransientAPIError):
        return True
    if isinstance(exc, HTTPError):
        return exc.code in _TRANSIENT_CODES
    if isinstance(exc, (URLError, OSError, TimeoutError, ConnectionError)):
        return True
    # tidalapi raises TooManyRequests (a 429) but rewrites its message to
    # "Album unavailable" in album.py, so string matching alone misses it.
    # Match by type name to stay robust to the message text.
    type_name = type(exc).__name__
    if "TooManyRequests" in type_name:
        return True
    # musicbrainzngs raises NetworkError (connection issues) and ResponseError
    # (wraps HTTP errors including 503)
    if "NetworkError" in type_name:
        return True
    # musicbrainzngs raises ResponseError with 503
    exc_str = str(exc)
    if "503" in exc_str or "Service Unavailable" in exc_str:
        return True
    if "429" in exc_str or "Too Many Requests" in exc_str:
        return True
    return False


def is_permanent(exc: Exception) -> bool:
    """Determine if an error is permanent (not retryable)."""
    if isinstance(exc, PermanentAPIError):
        return True
    if isinstance(exc, HTTPError):
        return exc.code in _PERMANENT_CODES
    # A rate limit is never permanent, even though tidalapi mislabels the
    # message as "Album unavailable". Check before any name-based matching.
    type_name = type(exc).__name__
    if "TooManyRequests" in type_name:
        return False
    # tidalapi ObjectNotFound
    if "ObjectNotFound" in type_name or "NotFound" in type_name:
        return True
    return False


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """Compute backoff delay with jitter for a given attempt number."""
    delay = min(config.base_delay * (2**attempt), config.max_delay)
    jitter = delay * config.jitter_factor
    return delay + random.uniform(-jitter, jitter)


def _get_reset_wait(exc: Exception) -> float | None:
    """Extract wait time from rate limit headers if available."""
    headers: dict = {}
    if isinstance(exc, TransientAPIError):
        headers = exc.headers
    elif isinstance(exc, HTTPError):
        headers = dict(exc.headers) if exc.headers else {}

    reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
    if reset:
        try:
            reset_time = float(reset)
            wait = max(1.0, reset_time - time.time())
            return wait
        except (ValueError, TypeError):
            pass
    return None


def retry_api_call(
    fn: Callable[..., T],
    *args,
    config: RetryConfig | None = None,
    stats: RetryStats | None = None,
    **kwargs,
) -> T:
    """Call fn with retry + backoff on transient errors.

    - On transient error (429, 5xx, timeout): retries with exponential backoff
    - On 429 with X-RateLimit-Reset header: waits until reset time
    - On permanent error (401, 403, 404): raises immediately
    - On exhausted retries or per-track timeout: raises last exception
    - Adds jitter to backoff to avoid thundering herd

    Args:
        fn: The callable to execute.
        *args: Positional arguments for fn.
        config: Retry configuration (defaults to RetryConfig()).
        stats: Optional stats accumulator for reporting.
        **kwargs: Keyword arguments for fn.

    Returns:
        The return value of fn on success.

    Raises:
        PermanentAPIError: On permanent errors.
        TransientAPIError: After retries exhausted.
        Exception: The last exception if retries exhausted.
    """
    if config is None:
        config = RetryConfig()

    start_time = time.monotonic()
    last_exc: Exception | None = None

    for attempt in range(config.max_retries + 1):
        # Check per-track timeout
        elapsed = time.monotonic() - start_time
        if attempt > 0 and elapsed >= config.per_track_timeout:
            if stats:
                stats.transient_failures += 1
            raise last_exc  # type: ignore[misc]

        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc

            # Permanent errors: raise immediately
            if is_permanent(exc):
                if stats:
                    stats.permanent_failures += 1
                raise

            # Not transient either: raise immediately
            if not is_transient(exc):
                raise

            # Last attempt: no more retries
            if attempt >= config.max_retries:
                if stats:
                    stats.transient_failures += 1
                raise

            # Compute wait time
            reset_wait = _get_reset_wait(exc)
            if reset_wait is not None:
                delay = min(reset_wait, config.per_track_timeout - elapsed)
                if stats:
                    stats.rate_limit_waits += 1
            else:
                delay = _compute_delay(attempt, config)

            # Respect per-track timeout
            remaining_budget = config.per_track_timeout - elapsed
            delay = min(delay, remaining_budget)

            if delay <= 0:
                if stats:
                    stats.transient_failures += 1
                raise

            if stats:
                stats.retries += 1
                stats.total_wait_seconds += delay

            time.sleep(delay)

    # Should not reach here, but just in case
    raise last_exc  # type: ignore[misc]
