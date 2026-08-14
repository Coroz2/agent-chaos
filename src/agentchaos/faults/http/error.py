"""Injected HTTP-error execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentchaos.config.models import HttpErrorFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultOutcome,
    SyntheticHttpResponse,
)


@dataclass(frozen=True, slots=True)
class HttpErrorExecutor:
    """Return the existing deterministic synthetic HTTP-error response."""

    status_code: int
    fault_type: Literal["http_error"] = field(default="http_error", init=False)

    @classmethod
    def from_config(cls, fault: HttpErrorFault) -> HttpErrorExecutor:
        return cls(status_code=fault.status_code)

    def event_parameters(self) -> dict[str, int]:
        return {"status_code": self.status_code}

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome:
        del context
        return HttpFaultOutcome(
            action=HttpFaultAction.RESPOND,
            response=SyntheticHttpResponse(
                content=b'{"error":"injected by Agent Chaos"}',
                status_code=self.status_code,
                headers=(("X-Agent-Chaos-Fault", "http_error"),),
                media_type="application/json",
            ),
            failure_kind="injected_http_error",
            status_code=self.status_code,
        )
