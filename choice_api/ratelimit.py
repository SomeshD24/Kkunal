"""
Thread-safe token bucket used to pace outbound API calls.

The broker throttles with HTTP 429. Pacing requests up front is cheaper than
being throttled, and for orders it doubles as a guard rail: exchanges require
strategies sending 10 or more orders per second to be registered as algos.
"""

import threading
import time
from typing import Optional


class TokenBucket:
    def __init__(self, rate: float, burst: Optional[float] = None):
        """
        Args:
            rate: Sustained calls per second.
            burst: Calls allowed back-to-back before pacing kicks in.
                Defaults to one second's worth.
        """
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.capacity = float(burst if burst is not None else max(1.0, rate))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Blocks until a call is allowed. Returns the seconds spent waiting."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                sleep_for = (1.0 - self._tokens) / self.rate
            time.sleep(sleep_for)
            waited += sleep_for

    def penalise(self, seconds: float) -> None:
        """Drains the bucket after a 429, so every thread backs off together."""
        with self._lock:
            self._tokens = min(self._tokens, 0.0) - max(0.0, seconds) * self.rate
