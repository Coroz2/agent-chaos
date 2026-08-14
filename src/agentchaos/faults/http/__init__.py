"""Private HTTP fault executor dispatch."""

from __future__ import annotations

from typing import assert_never

from agentchaos.config.models import FaultConfig, HttpErrorFault, HttpLatencyFault
from agentchaos.faults.http.base import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultExecutor,
    HttpFaultOutcome,
    SyntheticHttpResponse,
)
from agentchaos.faults.http.error import HttpErrorExecutor
from agentchaos.faults.http.latency import HttpLatencyExecutor
from agentchaos.faults.http.target import HttpTargetMatcher


def build_http_fault_executor(fault: FaultConfig) -> HttpFaultExecutor:
    """Exhaustively select an executor for one validated HTTP fault."""
    if isinstance(fault, HttpLatencyFault):
        return HttpLatencyExecutor.from_config(fault)
    if isinstance(fault, HttpErrorFault):
        return HttpErrorExecutor.from_config(fault)
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
