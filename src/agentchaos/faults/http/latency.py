"""Injected HTTP-latency execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from agentchaos.config.models import HttpLatencyFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultOutcome,
)


@dataclass(frozen=True, slots=True)
class HttpLatencyExecutor:
    """Delay forwarding unless a retry or client disconnect abandons the operation."""

    latency_ms: int
    fault_type: Literal["http_latency"] = field(default="http_latency", init=False)

    @classmethod
    def from_config(cls, fault: HttpLatencyFault) -> HttpLatencyExecutor:
        return cls(latency_ms=fault.latency_ms)

    def event_parameters(self) -> dict[str, int]:
        return {"latency_ms": self.latency_ms}

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome:
        delay_task = asyncio.create_task(asyncio.sleep(self.latency_ms / 1000))
        retry_task = asyncio.create_task(context.retry_seen.wait())
        done, pending = await asyncio.wait(
            {delay_task, retry_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        abandoned = retry_task in done
        if not abandoned:
            abandoned = not await context.begin_forwarding()
        if not abandoned:
            abandoned = await context.is_disconnected()
        if abandoned:
            return HttpFaultOutcome(
                action=HttpFaultAction.ABANDON,
                failure_kind="client_disconnected",
            )
        return HttpFaultOutcome(action=HttpFaultAction.FORWARD)
