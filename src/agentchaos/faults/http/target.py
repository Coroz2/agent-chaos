"""HTTP fault target matching."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentchaos.config.models import FaultTarget


def path_matches(pattern: str, path: str) -> bool:
    """Match a path using only '*' as a wildcard."""
    expression = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(expression, path) is not None


@dataclass(frozen=True, slots=True)
class HttpTargetMatcher:
    """Match a request method and concrete path against a validated target."""

    method: str | None
    path: str

    @classmethod
    def from_config(cls, target: FaultTarget) -> HttpTargetMatcher:
        return cls(method=target.method, path=target.path)

    def matches(self, method: str, path: str) -> bool:
        if self.method is not None and self.method != method.upper():
            return False
        return path_matches(self.path, path)
