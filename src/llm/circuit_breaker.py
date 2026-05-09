import asyncio
import time


class CircuitBreakerOpen(Exception):
    """熔断器打开，拒绝请求"""


class CircuitBreaker:
    """简单熔断器：失败率超阈值 → OPEN → 拒绝请求 → 窗口后 HALF_OPEN → 试探"""

    def __init__(self, threshold: float, window_sec: int) -> None:
        self.threshold = threshold
        self.window_sec = window_sec
        self.state = "CLOSED"  # CLOSED | OPEN | HALF_OPEN
        self._successes = 0
        self._failures = 0
        self._opened_at = 0.0
        self._window_started_at = time.time()
        self._lock = asyncio.Lock()

    async def check(self) -> None:
        async with self._lock:
            if self.state == "CLOSED":
                return
            if self.state == "OPEN":
                if time.time() - self._opened_at >= self.window_sec:
                    self.state = "HALF_OPEN"
                    return
                raise CircuitBreakerOpen("Circuit breaker is OPEN")
            # HALF_OPEN: 允许一次试探
            return

    async def record_success(self) -> None:
        async with self._lock:
            if self.state == "HALF_OPEN":
                self._reset()
                return
            self._reset_closed_window_if_expired()
            self._successes += 1

    async def record_failure(self) -> None:
        async with self._lock:
            if self.state == "HALF_OPEN":
                self._trip()
                return
            self._reset_closed_window_if_expired()
            self._failures += 1
            total = self._successes + self._failures
            if total >= 3 and self._failures / total > self.threshold:
                self._trip()

    def _trip(self) -> None:
        self.state = "OPEN"
        self._opened_at = time.time()

    def _reset(self) -> None:
        self.state = "CLOSED"
        self._successes = 0
        self._failures = 0
        self._opened_at = 0.0
        self._window_started_at = time.time()

    def _reset_closed_window_if_expired(self) -> None:
        if self.state == "CLOSED" and time.time() - self._window_started_at >= self.window_sec:
            self._successes = 0
            self._failures = 0
            self._window_started_at = time.time()
