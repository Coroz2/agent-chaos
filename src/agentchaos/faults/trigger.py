"""Deterministic fault triggers."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field


def path_matches(pattern: str, path: str) -> bool:
    """Match a path using only '*' as a wildcard."""
    expression = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(expression, path) is not None


@dataclass(slots=True)
class OccurrenceTrigger:
    """Fire exactly once on a configured matching occurrence."""

    occurrence: int
    _count: int = 0
    _fired: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def count(self) -> int:
        return self._count

    @property
    def fired(self) -> bool:
        return self._fired

    async def evaluate(self) -> bool:
        async with self._lock:
            self._count += 1
            if not self._fired and self._count == self.occurrence:
                self._fired = True
                return True
            return False
