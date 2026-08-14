"""Injected HTTP rate-limit execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentchaos.config.models import HttpRateLimitFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultOutcome,
    SyntheticHttpResponse,
)


@dataclass(frozen=True, slots=True)
class HttpRateLimitExecutor:
    """Return the deterministic synthetic HTTP 429 response."""

    retry_after_seconds: int
    fault_type: Literal["http_rate_limit"] = field(default="http_rate_limit", init=False)

    @classmethod
    def from_config(cls, fault: HttpRateLimitFault) -> HttpRateLimitExecutor:
        return cls(retry_after_seconds=fault.retry_after_seconds)

    def event_parameters(self) -> dict[str, int]:
        return {"retry_after_seconds": self.retry_after_seconds}

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome:
        del context
        return HttpFaultOutcome(
            action=HttpFaultAction.RESPOND,
            response=SyntheticHttpResponse(
                content=b'{"error": "rate limited by Agent Chaos"}',
                status_code=429,
                headers=(
                    ("Retry-After", str(self.retry_after_seconds)),
                    ("X-Agent-Chaos-Fault", "http_rate_limit"),
                ),
                media_type="application/json",
            ),
            failure_kind="injected_rate_limit",
            status_code=429,
        )
