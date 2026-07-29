"""简易熔断器：连续失败达阈值后短时拒绝，防止雪崩打满上游。"""

from __future__ import annotations

import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, *, fail_threshold: int = 5, reset_seconds: float = 30.0) -> None:
        self.fail_threshold = max(fail_threshold, 1)
        self.reset_seconds = reset_seconds
        self._failures = 0
        self._opened_at = 0.0
        self.state = CircuitState.CLOSED

    def allow(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.reset_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # half-open：放行一次试探

    def record_success(self) -> None:
        self._failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.fail_threshold or self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._opened_at = time.monotonic()
