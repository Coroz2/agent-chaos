"""Deterministic fault triggers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from agentchaos.faults.http.target import path_matches

__all__ = ["OccurrenceTrigger", "path_matches"]


@dataclass(slots=True)
class OccurrenceTrigger:
    """Select every configured matching-request occurrence exactly once."""

    occurrence: int | tuple[int, ...]
    _count: int = 0
    _next_index: int = 0
    _schedule: tuple[int, ...] = field(default=(), init=False)
    _completed_occurrences: list[int] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        configured = self.occurrence
        if isinstance(configured, bool):
            raise ValueError("occurrences must contain positive integers")
        if isinstance(configured, int):
            configured = (configured,)
        if not configured or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in configured
        ):
            raise ValueError("occurrences must contain positive integers")
        if any(
            current >= following
            for current, following in zip(configured, configured[1:], strict=False)
        ):
            raise ValueError("occurrences must be in strictly increasing order")
        self._schedule = configured

    @property
    def count(self) -> int:
        return self._count

    @property
    def fired(self) -> bool:
        return bool(self._completed_occurrences)

    @property
    def completed_occurrences(self) -> tuple[int, ...]:
        return tuple(self._completed_occurrences)

    @property
    def occurrences(self) -> tuple[int, ...]:
        return self._schedule

    @property
    def complete(self) -> bool:
        return self._next_index == len(self._schedule)

    async def evaluate(self) -> bool:
        async with self._lock:
            self._count += 1
            if (
                self._next_index < len(self._schedule)
                and self._count == self._schedule[self._next_index]
            ):
                self._completed_occurrences.append(self._count)
                self._next_index += 1
                return True
            return False
