from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .schemas import AgentDescriptor


@dataclass
class _Health:
    consecutive_failures: int = 0
    opened_at: float | None = None


class CandidateHealthTracker:
    """In-process circuit breaker for dynamically discovered candidates."""

    def __init__(self, *, failure_threshold: int = 2, cooldown_seconds: float = 60.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self._health: dict[str, _Health] = {}
        self._lock = threading.RLock()

    def available(self, candidates: list[AgentDescriptor]) -> list[AgentDescriptor]:
        now = time.monotonic()
        available: list[AgentDescriptor] = []
        blocked: list[AgentDescriptor] = []
        with self._lock:
            for candidate in candidates:
                health = self._health.get(candidate.aic)
                if (
                    health is not None
                    and health.opened_at is not None
                    and now - health.opened_at < self.cooldown_seconds
                ):
                    blocked.append(candidate)
                else:
                    available.append(candidate)
        # If every candidate is open, allow the highest-ranked one as a
        # half-open probe instead of making recovery impossible.
        return available or blocked[:1]

    def success(self, candidate: AgentDescriptor) -> None:
        with self._lock:
            self._health[candidate.aic] = _Health()

    def failure(self, candidate: AgentDescriptor) -> None:
        with self._lock:
            health = self._health.setdefault(candidate.aic, _Health())
            health.consecutive_failures += 1
            if health.consecutive_failures >= self.failure_threshold:
                health.opened_at = time.monotonic()
