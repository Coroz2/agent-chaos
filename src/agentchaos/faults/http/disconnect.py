"""Injected HTTP connection-disruption execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agentchaos.config.models import HttpDisconnectFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultOutcome,
)


@dataclass(frozen=True, slots=True)
class HttpDisconnectExecutor:
    """Select the private proxy disposition that aborts the client connection."""

    fault_type: Literal["http_disconnect"] = field(default="http_disconnect", init=False)

    @classmethod
    def from_config(cls, fault: HttpDisconnectFault) -> HttpDisconnectExecutor:
        del fault
        return cls()

    def event_parameters(self) -> dict[str, int]:
        return {}

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome:
        del context
        return HttpFaultOutcome(
            action=HttpFaultAction.DISCONNECT,
            failure_kind="injected_disconnect",
        )
