from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Literal

from .exceptions import RateLimitExceeded

logger = __import__("logging").getLogger(__name__)


class RateLimiter:
    """Sliding window rate limiter for API requests."""

    def __init__(
        self,
        rpm: int = 1000,
        rpd: int = 1000000,
    ):
        self.rpm = rpm
        self.rpd = rpd
        self._minute_window: deque[float] = deque()
        self._day_window: deque[float] = deque()

    def _cleanup(self, now: float) -> None:
        cutoff_minute = now - 60
        cutoff_day = now - 86400
        while self._minute_window and self._minute_window[0] < cutoff_minute:
            self._minute_window.popleft()
        while self._day_window and self._day_window[0] < cutoff_day:
            self._day_window.popleft()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            self._cleanup(now)

            if len(self._minute_window) >= self.rpm:
                wait_time = 60 - (now - self._minute_window[0])
                if wait_time > 0:
                    logger.debug(
                        "Rate limit: RPM reached (%d/%d), waiting %.1fs",
                        len(self._minute_window),
                        self.rpm,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time + 0.1)
                    continue

            if len(self._day_window) >= self.rpd:
                wait_time = 86400 - (now - self._day_window[0])
                if wait_time > 0:
                    logger.debug(
                        "Rate limit: RPD reached (%d/%d), waiting %.0fs",
                        len(self._day_window),
                        self.rpd,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time + 1)
                    continue

            self._minute_window.append(now)
            self._day_window.append(now)
            return
