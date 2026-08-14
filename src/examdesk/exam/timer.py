from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

from .state import ExamState


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class ExamTimer:
    WARNING_SECONDS = (600, 300, 60)

    def __init__(self, state: ExamState, clock: Clock | None = None) -> None:
        self.state = state
        self.clock = clock or SystemClock()
        self.anchor_wall = max(self.clock.now().astimezone(UTC), state.last_effective_at)
        self.anchor_monotonic = self.clock.monotonic()
        if self.clock.now().astimezone(UTC) < state.last_effective_at:
            state.time_anomaly = True

    def effective_now(self) -> datetime:
        wall_now = self.clock.now().astimezone(UTC)
        elapsed = max(0.0, self.clock.monotonic() - self.anchor_monotonic)
        monotonic_now = self.anchor_wall + timedelta(seconds=elapsed)
        if wall_now < self.state.last_effective_at:
            self.state.time_anomaly = True
        return max(wall_now, monotonic_now, self.state.last_effective_at)

    def remaining_seconds(self) -> int | None:
        if self.state.deadline_at is None:
            return None
        return max(0, int((self.state.deadline_at - self.effective_now()).total_seconds()))

    def is_expired(self) -> bool:
        remaining = self.remaining_seconds()
        return remaining is not None and remaining <= 0

    def checkpoint(self) -> datetime:
        effective = self.effective_now()
        self.state.last_effective_at = effective
        return effective

    def due_warnings(self) -> list[int]:
        remaining = self.remaining_seconds()
        if remaining is None:
            return []
        due = [
            threshold
            for threshold in self.WARNING_SECONDS
            if remaining <= threshold and threshold not in self.state.warnings_shown
        ]
        self.state.warnings_shown.update(due)
        return due

