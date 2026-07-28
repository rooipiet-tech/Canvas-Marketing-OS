"""mcp-web rate limiter — sliding-window request cap (AC-5, CO-1).

Ceiling and window are sourced from env vars (RATE_LIMIT_MAX,
RATE_LIMIT_WINDOW_SEC) rather than hardcoded literals, so they're tunable
per-environment without a code change (CO-1).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque


class RateLimitExceeded(Exception):
    """Raised when a call is rejected by the rate limiter."""


class SlidingWindowRateLimiter:
    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> None:
        """Raise RateLimitExceeded if this call would exceed the ceiling
        within the current window; otherwise record it and allow it."""
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > self.window_seconds:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                raise RateLimitExceeded(
                    f"rate limit exceeded: {self.max_calls} calls per {self.window_seconds}s"
                )
            self._calls.append(now)

    def reset(self) -> None:
        """Test/debug helper: clear recorded call history."""
        with self._lock:
            self._calls.clear()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def build_default_rate_limiter() -> SlidingWindowRateLimiter:
    """Reads RATE_LIMIT_MAX / RATE_LIMIT_WINDOW_SEC from the environment
    (not hardcoded — CO-1) at call time, so tests/deployments can tune
    them without a code change."""
    max_calls = _env_int("RATE_LIMIT_MAX", 5)
    window_seconds = _env_float("RATE_LIMIT_WINDOW_SEC", 1.0)
    return SlidingWindowRateLimiter(max_calls=max_calls, window_seconds=window_seconds)
