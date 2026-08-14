"""Injected malformed-JSON execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentchaos.config.models import HttpMalformedJsonFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultOutcome,
    SyntheticHttpResponse,
)

MALFORMED_JSON_BODY = b'{"error":"injected by Agent Chaos"'


@dataclass(frozen=True, slots=True)
class HttpMalformedJsonExecutor:
    """Return Agent Chaos's fixed deliberately invalid JSON response."""

    fault_type: Literal["http_malformed_json"] = field(default="http_malformed_json", init=False)

    @classmethod
    def from_config(cls, fault: HttpMalformedJsonFault) -> HttpMalformedJsonExecutor:
        del fault
        return cls()

    def event_parameters(self) -> dict[str, int]:
        return {}

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome:
        del context
        return HttpFaultOutcome(
            action=HttpFaultAction.RESPOND,
            response=SyntheticHttpResponse(
                content=MALFORMED_JSON_BODY,
                status_code=200,
                headers=(("X-Agent-Chaos-Fault", "http_malformed_json"),),
                media_type="application/json",
            ),
            failure_kind="injected_malformed_json",
            status_code=200,
        )
