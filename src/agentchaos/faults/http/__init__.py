"""Private HTTP fault executor dispatch."""

from __future__ import annotations

from typing import assert_never

from agentchaos.config.models import (
    FaultConfig,
    HttpDisconnectFault,
    HttpErrorFault,
    HttpLatencyFault,
    HttpMalformedJsonFault,
    HttpRateLimitFault,
)
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultExecutor,
    HttpFaultOutcome,
    SyntheticHttpResponse,
)
from agentchaos.faults.http.disconnect import HttpDisconnectExecutor
from agentchaos.faults.http.error import HttpErrorExecutor
from agentchaos.faults.http.latency import HttpLatencyExecutor
from agentchaos.faults.http.malformed_json import HttpMalformedJsonExecutor
from agentchaos.faults.http.rate_limit import HttpRateLimitExecutor
from agentchaos.faults.http.target import HttpTargetMatcher


def build_http_fault_executor(fault: FaultConfig) -> HttpFaultExecutor:
    """Exhaustively select an executor for one validated HTTP fault."""
    if isinstance(fault, HttpLatencyFault):
        return HttpLatencyExecutor.from_config(fault)
    if isinstance(fault, HttpErrorFault):
        return HttpErrorExecutor.from_config(fault)
    if isinstance(fault, HttpRateLimitFault):
        return HttpRateLimitExecutor.from_config(fault)
    if isinstance(fault, HttpMalformedJsonFault):
        return HttpMalformedJsonExecutor.from_config(fault)
    if isinstance(fault, HttpDisconnectFault):
        return HttpDisconnectExecutor.from_config(fault)
    assert_never(fault)


__all__ = [
    "HttpFaultAction",
    "HttpFaultExecutionContext",
    "HttpFaultExecutor",
    "HttpFaultOutcome",
    "HttpTargetMatcher",
    "SyntheticHttpResponse",
    "build_http_fault_executor",
]
