"""Deterministic fault triggers."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from agentchaos.config.models import (
    OccurrenceTriggerConfig,
    ProbabilityTriggerConfig,
    TriggerConfig,
)
from agentchaos.faults.http.target import path_matches

__all__ = [
    "OccurrenceTrigger",
    "ProbabilityTrigger",
    "build_trigger",
    "path_matches",
    "probability_selects",
]

PROBABILITY_SCALE = 1_000_000


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


@dataclass(slots=True)
class ProbabilityTrigger:
    """Select matching occurrences reproducibly inside one inclusive window."""

    probability: Decimal
    seed: int
    start_occurrence: int
    end_occurrence: int
    _count: int = 0
    _evaluated_occurrences: int = 0
    _selected_occurrences: list[int] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        if not self.probability.is_finite() or self.probability <= 0 or self.probability > 1:
            raise ValueError("probability must be greater than 0 and at most 1")
        exponent = self.probability.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -6:
            raise ValueError("probability supports at most six decimal places")
        if isinstance(self.seed, bool) or not 0 <= self.seed <= (2**64) - 1:
            raise ValueError("seed must be an unsigned 64-bit integer")
        if self.start_occurrence <= 0 or self.end_occurrence < self.start_occurrence:
            raise ValueError("probability window bounds are invalid")

    @property
    def count(self) -> int:
        return self._count

    @property
    def fired(self) -> bool:
        return bool(self._selected_occurrences)

    @property
    def evaluated_occurrences(self) -> int:
        return self._evaluated_occurrences

    @property
    def selected_occurrences(self) -> tuple[int, ...]:
        return tuple(self._selected_occurrences)

    @property
    def complete(self) -> bool:
        return self._count >= self.end_occurrence

    async def evaluate(self) -> bool:
        async with self._lock:
            self._count += 1
            occurrence = self._count
            if occurrence < self.start_occurrence or occurrence > self.end_occurrence:
                return False
            self._evaluated_occurrences += 1
            if probability_selects(self.probability, self.seed, occurrence):
                self._selected_occurrences.append(occurrence)
                return True
            return False


type Trigger = OccurrenceTrigger | ProbabilityTrigger


def build_trigger(config: TriggerConfig) -> Trigger:
    if isinstance(config, OccurrenceTriggerConfig):
        return OccurrenceTrigger(config.schedule)
    assert isinstance(config, ProbabilityTriggerConfig)
    return ProbabilityTrigger(
        probability=config.probability,
        seed=config.seed,
        start_occurrence=config.window.start_occurrence,
        end_occurrence=config.window.end_occurrence,
    )


def probability_selects(probability: Decimal, seed: int, occurrence: int) -> bool:
    """Return the portable seeded selection for one positive occurrence."""
    millionths = int(probability * PROBABILITY_SCALE)
    threshold = millionths * (1 << 256) // PROBABILITY_SCALE
    material = f"agentchaos-probability-v1:{seed}:{occurrence}".encode("ascii")
    value = int.from_bytes(hashlib.sha256(material).digest(), "big")
    return value < threshold
