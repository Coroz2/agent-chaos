import asyncio
import json

import pytest

from agentchaos.config.models import (
    FaultTarget,
    HttpErrorFault,
    HttpLatencyFault,
    HttpMalformedJsonFault,
    HttpRateLimitFault,
)
from agentchaos.faults.http import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpTargetMatcher,
    build_http_fault_executor,
)


def latency_fault(latency_ms: int = 1) -> HttpLatencyFault:
    return HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "latency_ms": latency_ms,
        }
    )


def error_fault(status_code: int = 503) -> HttpErrorFault:
    return HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "status_code": status_code,
        }
    )


def rate_limit_fault(retry_after_seconds: int = 1) -> HttpRateLimitFault:
    return HttpRateLimitFault.model_validate(
        {
            "type": "http_rate_limit",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "retry_after_seconds": retry_after_seconds,
        }
    )


def malformed_json_fault() -> HttpMalformedJsonFault:
    return HttpMalformedJsonFault.model_validate(
        {
            "type": "http_malformed_json",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 1},
        }
    )


@pytest.mark.asyncio
async def test_dispatches_latency_executor() -> None:
    executor = build_http_fault_executor(latency_fault())
    retry_seen = asyncio.Event()

    outcome = await executor.execute(
        HttpFaultExecutionContext(
            retry_seen=retry_seen,
            is_disconnected=_connected,
        )
    )

    assert executor.fault_type == "http_latency"
    assert executor.event_parameters() == {"latency_ms": 1}
    assert outcome.action == HttpFaultAction.FORWARD
    assert outcome.response is None
    assert outcome.failure_kind is None
    assert outcome.status_code is None


@pytest.mark.asyncio
async def test_dispatches_http_error_executor() -> None:
    executor = build_http_fault_executor(error_fault())
    retry_seen = asyncio.Event()

    outcome = await executor.execute(
        HttpFaultExecutionContext(
            retry_seen=retry_seen,
            is_disconnected=_connected,
        )
    )

    assert executor.fault_type == "http_error"
    assert executor.event_parameters() == {"status_code": 503}
    assert outcome.action == HttpFaultAction.RESPOND
    assert outcome.failure_kind == "injected_http_error"
    assert outcome.status_code == 503
    assert outcome.response is not None
    assert outcome.response.status_code == 503
    assert outcome.response.content == b'{"error":"injected by Agent Chaos"}'
    assert outcome.response.headers == (("X-Agent-Chaos-Fault", "http_error"),)
    assert outcome.response.media_type == "application/json"


@pytest.mark.asyncio
async def test_dispatches_rate_limit_executor_with_exact_response() -> None:
    executor = build_http_fault_executor(rate_limit_fault(retry_after_seconds=0))

    outcome = await executor.execute(
        HttpFaultExecutionContext(
            retry_seen=asyncio.Event(),
            is_disconnected=_connected,
        )
    )

    assert executor.fault_type == "http_rate_limit"
    assert executor.event_parameters() == {"retry_after_seconds": 0}
    assert outcome.action == HttpFaultAction.RESPOND
    assert outcome.failure_kind == "injected_rate_limit"
    assert outcome.status_code == 429
    assert outcome.response is not None
    assert outcome.response.status_code == 429
    assert outcome.response.content == b'{"error": "rate limited by Agent Chaos"}'
    assert outcome.response.headers == (
        ("Retry-After", "0"),
        ("X-Agent-Chaos-Fault", "http_rate_limit"),
    )
    assert outcome.response.media_type == "application/json"


@pytest.mark.asyncio
async def test_dispatches_http_malformed_json_executor() -> None:
    executor = build_http_fault_executor(malformed_json_fault())

    outcome = await executor.execute(
        HttpFaultExecutionContext(
            retry_seen=asyncio.Event(),
            is_disconnected=_connected,
        )
    )

    assert executor.fault_type == "http_malformed_json"
    assert executor.event_parameters() == {}
    assert outcome.action == HttpFaultAction.RESPOND
    assert outcome.failure_kind == "injected_malformed_json"
    assert outcome.status_code == 200
    assert outcome.response is not None
    assert outcome.response.status_code == 200
    assert outcome.response.content == b'{"error":"injected by Agent Chaos"'
    assert outcome.response.headers == (("X-Agent-Chaos-Fault", "http_malformed_json"),)
    assert outcome.response.media_type == "application/json"
    with pytest.raises(json.JSONDecodeError):
        json.loads(outcome.response.content)


def test_http_target_matching_is_separate_from_triggering() -> None:
    target = FaultTarget(method="GET", path="/customer/*")
    matcher = HttpTargetMatcher.from_config(target)

    assert matcher.matches("get", "/customer/123")
    assert not matcher.matches("POST", "/customer/123")
    assert not matcher.matches("GET", "/orders/123")


async def _connected() -> bool:
    return False
