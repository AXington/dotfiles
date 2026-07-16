import time


class RateLimiter:
    """Rate limiter with fixed-interval and adaptive (header-based) modes.

    Fixed mode: enforces a minimum interval between calls (for APIs without
    rate limit headers, like Qobuz).

    Adaptive mode: reads X-RateLimit-Remaining and X-RateLimit-Reset headers
    from responses and adjusts pace dynamically. Handles 429 backoff.
    """

    def __init__(
        self,
        max_per_second: float = 4.0,
        *,
        calls_per_second: float | None = None,
        adaptive: bool = False,
    ):
        if calls_per_second is not None:
            max_per_second = calls_per_second
        self._min_interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._adaptive = adaptive
        self._remaining: int | None = None
        self._reset_at: float | None = None
        self._backoff_until: float = 0.0

    def acquire(self) -> bool:
        """Try to acquire permission for a call without blocking."""
        now = time.monotonic()
        if now < self._backoff_until:
            return False
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            return False
        self._last_call = now
        return True

    def wait_time(self) -> float:
        """Return the remaining delay before the next call is allowed."""
        now = time.monotonic()
        if now < self._backoff_until:
            return self._backoff_until - now
        elapsed = now - self._last_call
        return max(0.0, self._min_interval - elapsed)

    def wait(self) -> None:
        """Block until the next call is allowed."""
        delay = self.wait_time()
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.monotonic()

    def update_from_headers(self, headers: dict) -> None:
        """Update rate limit state from response headers.

        Reads X-RateLimit-Remaining and X-RateLimit-Reset.
        Adjusts pacing when remaining calls are low.
        """
        if not self._adaptive:
            return

        remaining = headers.get("X-RateLimit-Remaining") or headers.get(
            "x-ratelimit-remaining"
        )
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")

        if remaining is not None:
            try:
                self._remaining = int(remaining)
            except (ValueError, TypeError):
                pass

        if reset is not None:
            try:
                self._reset_at = float(reset)
            except (ValueError, TypeError):
                pass

        # Adaptive pacing: slow down when running low
        if self._remaining is not None and self._remaining < 10:
            # Spread remaining calls across time until reset
            if self._reset_at:
                now = time.time()
                time_until_reset = max(1.0, self._reset_at - now)
                if self._remaining > 0:
                    self._min_interval = time_until_reset / self._remaining
                else:
                    self._min_interval = time_until_reset

    def handle_429(self, headers: dict) -> float:
        """Handle a 429 response. Returns seconds to wait.

        Reads X-RateLimit-Reset to determine when to retry.
        """
        reset = headers.get("X-RateLimit-Reset") or headers.get("x-ratelimit-reset")
        if reset:
            try:
                reset_time = float(reset)
                wait_seconds = max(1.0, reset_time - time.time())
                self._backoff_until = time.monotonic() + wait_seconds
                return wait_seconds
            except (ValueError, TypeError):
                pass
        # Default backoff: 60 seconds
        self._backoff_until = time.monotonic() + 60.0
        return 60.0

    @property
    def remaining(self) -> int | None:
        """Number of requests remaining in current window (if known)."""
        return self._remaining
