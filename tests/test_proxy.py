import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentchaos.analysis.analyzer import analyze
from agentchaos.config.models import (
    FaultConfig,
    HttpDisconnectFault,
    HttpErrorFault,
    HttpLatencyFault,
    HttpMalformedJsonFault,
    HttpRateLimitFault,
    Scenario,
)
from agentchaos.events.models import (
    EventType,
    OperationSucceededPayload,
    WorkloadCompletedPayload,
)
from agentchaos.events.recorder import EventRecorder
from agentchaos.proxy import server as proxy_module
from agentchaos.proxy.protocol import AgentChaosH11Protocol
from agentchaos.proxy.server import ChaosProxy
from agentchaos.runtime.orchestrator import _build_report

UPSTREAM_PATHS: list[str] = []
UPSTREAM_REQUESTS: list[dict[str, str]] = []


async def echo(request: Request) -> JSONResponse:
    body = await request.body()
    UPSTREAM_PATHS.append(request.url.path)
    UPSTREAM_REQUESTS.append(
        {
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "body": body.decode(),
            "authorization": request.headers.get("authorization", ""),
            "host": request.headers.get("host", ""),
            "te": request.headers.get("te", ""),
            "x-test": request.headers.get("x-test", ""),
        }
    )
    return JSONResponse(
        {
            "method": request.method,
            "path": request.url.path,
            "header": request.headers.get("x-test"),
        },
        headers={"X-Upstream": "preserved", "Connection": "close"},
    )


async def large_response(request: Request) -> JSONResponse:
    return JSONResponse({"value": "12345"})


async def slow_response(request: Request) -> JSONResponse:
    UPSTREAM_PATHS.append(request.url.path)
    await asyncio.sleep(0.1)
    return JSONResponse({"path": request.url.path})


UPSTREAM = Starlette(
    routes=[
        Route("/large", large_response),
        Route("/slow", slow_response),
        Route("/{path:path}", echo, methods=["GET", "POST"]),
    ]
)


@asynccontextmanager
async def running_proxy(
    tmp_path: Path, fault: FaultConfig | None
) -> AsyncIterator[tuple[ChaosProxy, EventRecorder]]:
    recorder = EventRecorder(tmp_path / "events.jsonl", "run")
    proxy = ChaosProxy("http://upstream", fault, recorder)
    await proxy._client.aclose()
    proxy._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=UPSTREAM), trust_env=False)
    try:
        await proxy.start()
        yield proxy, recorder
    finally:
        await proxy.stop()
        recorder.close()


@pytest.mark.asyncio
async def test_proxy_forwards_and_injects_http_error(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 2},
            "status_code": 503,
        }
    )
    UPSTREAM_PATHS.clear()
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            first = await client.get("/customer/123", headers={"X-Test": "preserved"})
            second = await client.get("/customer/123")
            third = await client.get("/customer/123")

    assert first.json()["header"] == "preserved"
    assert second.status_code == 503
    assert second.content == b'{"error":"injected by Agent Chaos"}'
    assert second.headers["content-type"] == "application/json"
    assert second.headers["x-agent-chaos-fault"] == "http_error"
    assert third.status_code == 200
    assert UPSTREAM_PATHS == ["/customer/123", "/customer/123"]
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_OBSERVED,
        EventType.RETRY_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[3].payload
    failed = recorder.events[4].payload
    retry = recorder.events[6].payload
    assert injected.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": injected.operation_id,
        "fault_type": "http_error",
        "parameters": {"status_code": 503},
    }
    assert failed.model_dump(mode="json") == {
        "kind": "OPERATION_FAILED",
        "operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "failure_kind": "injected_http_error",
        "status_code": 503,
        "fault_related": True,
    }
    assert retry.model_dump(mode="json") == {
        "kind": "RETRY_OBSERVED",
        "operation_id": retry.operation_id,
        "retry_of_operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "attempt": 2,
    }


@pytest.mark.asyncio
async def test_proxy_injects_every_scheduled_occurrence(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrences": [2, 4]},
            "status_code": 503,
        }
    )
    UPSTREAM_PATHS.clear()

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            responses = [await client.get("/customer/123") for _ in range(5)]

    assert [response.status_code for response in responses] == [200, 503, 200, 503, 200]
    assert UPSTREAM_PATHS == ["/customer/123"] * 3
    injected = [event for event in recorder.events if event.event_type == EventType.FAULT_INJECTED]
    retries = [event for event in recorder.events if event.event_type == EventType.RETRY_OBSERVED]
    assert len(injected) == 2
    assert [event.payload.retry_of_operation_id for event in retries] == [
        injected[0].payload.operation_id,
        injected[1].payload.operation_id,
    ]
    assert proxy.trigger is not None
    assert proxy.trigger.completed_occurrences == (2, 4)


@pytest.mark.asyncio
async def test_adjacent_injections_use_fifo_recovery_links(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrences": [2, 3]},
            "status_code": 503,
        }
    )

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            responses = [await client.get("/customer/123") for _ in range(5)]

    assert [response.status_code for response in responses] == [200, 503, 503, 200, 200]
    injected = [event for event in recorder.events if event.event_type == EventType.FAULT_INJECTED]
    retries = [event for event in recorder.events if event.event_type == EventType.RETRY_OBSERVED]
    assert [event.payload.retry_of_operation_id for event in retries] == [
        injected[0].payload.operation_id,
        injected[0].payload.operation_id,
        injected[1].payload.operation_id,
    ]
    assert [event.payload.attempt for event in retries] == [2, 3, 2]


@pytest.mark.asyncio
async def test_probability_selected_retries_create_separate_fifo_failures(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {
                "probability": 0.5,
                "seed": 42,
                "window": {"start_occurrence": 1, "end_occurrence": 3},
            },
            "status_code": 503,
        }
    )

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            responses = [await client.get("/customer/123") for _ in range(5)]

    assert [response.status_code for response in responses] == [503, 503, 503, 200, 200]
    injected = [event for event in recorder.events if event.event_type == EventType.FAULT_INJECTED]
    retries = [event for event in recorder.events if event.event_type == EventType.RETRY_OBSERVED]
    assert [event.payload.retry_of_operation_id for event in retries] == [
        injected[0].payload.operation_id,
        injected[0].payload.operation_id,
        injected[0].payload.operation_id,
        injected[1].payload.operation_id,
    ]


@pytest.mark.asyncio
async def test_recovery_queues_are_independent_per_fingerprint(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrences": [1, 2]},
            "status_code": 503,
        }
    )

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            responses = [
                await client.get("/customer/123?key=one"),
                await client.get("/customer/123?key=two"),
                await client.get("/customer/123?key=two"),
                await client.get("/customer/123?key=one"),
            ]

    assert [response.status_code for response in responses] == [503, 503, 200, 200]
    injected = [event for event in recorder.events if event.event_type == EventType.FAULT_INJECTED]
    retries = [event for event in recorder.events if event.event_type == EventType.RETRY_OBSERVED]
    assert [event.payload.retry_of_operation_id for event in retries] == [
        injected[1].payload.operation_id,
        injected[0].payload.operation_id,
    ]


@pytest.mark.asyncio
async def test_concurrent_selection_follows_operation_observed_sequence(tmp_path: Path) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/concurrent"},
            "trigger": {"occurrences": [3, 7, 11]},
            "status_code": 503,
        }
    )
    recorder = EventRecorder(tmp_path / "events.jsonl", "run")
    proxy = ChaosProxy("http://upstream", fault, recorder)
    fingerprint = proxy._fingerprint("GET", "/concurrent", "query", "body")
    try:
        await asyncio.gather(
            *(
                proxy._begin_operation(
                    operation_id=f"operation-{index}",
                    method="GET",
                    path="/concurrent",
                    query_hash="query",
                    body_hash="body",
                    fingerprint=fingerprint,
                    can_inject=True,
                )
                for index in range(20)
            )
        )
    finally:
        await proxy._client.aclose()
        recorder.close()

    observed_ids = [
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.OPERATION_OBSERVED
    ]
    injected_ids = [
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.FAULT_INJECTED
    ]
    assert injected_ids == [observed_ids[2], observed_ids[6], observed_ids[10]]


@pytest.mark.asyncio
async def test_concurrent_probability_selection_follows_operation_observed_sequence(
    tmp_path: Path,
) -> None:
    fault = HttpErrorFault.model_validate(
        {
            "type": "http_error",
            "target": {"method": "GET", "path": "/concurrent"},
            "trigger": {
                "probability": 0.5,
                "seed": 10,
                "window": {"start_occurrence": 1, "end_occurrence": 5},
            },
            "status_code": 503,
        }
    )
    recorder = EventRecorder(tmp_path / "events.jsonl", "run")
    proxy = ChaosProxy("http://upstream", fault, recorder)
    fingerprint = proxy._fingerprint("GET", "/concurrent", "query", "body")
    try:
        await asyncio.gather(
            *(
                proxy._begin_operation(
                    operation_id=f"operation-{index}",
                    method="GET",
                    path="/concurrent",
                    query_hash="query",
                    body_hash="body",
                    fingerprint=fingerprint,
                    can_inject=True,
                )
                for index in range(8)
            )
        )
    finally:
        await proxy._client.aclose()
        recorder.close()

    observed_ids = [
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.OPERATION_OBSERVED
    ]
    injected_ids = [
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.FAULT_INJECTED
    ]
    assert injected_ids == [observed_ids[1], observed_ids[3]]


@pytest.mark.asyncio
async def test_proxy_injects_exact_rate_limit_response_without_contacting_upstream(
    tmp_path: Path,
) -> None:
    fault = HttpRateLimitFault.model_validate(
        {
            "type": "http_rate_limit",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 2},
            "retry_after_seconds": 0,
        }
    )
    header_secret = "rate-limit-header-secret"
    body_secret = "rate-limit-body-secret"
    query_secret = "rate-limit-query-secret"
    UPSTREAM_PATHS.clear()
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            request_kwargs = {
                "content": body_secret,
                "headers": {"Authorization": header_secret},
            }
            url = f"/customer/123?token={query_secret}"
            first = await client.request("GET", url, **request_kwargs)
            second = await client.request("GET", url, **request_kwargs)
            third = await client.request("GET", url, **request_kwargs)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.content == b'{"error": "rate limited by Agent Chaos"}'
    assert second.headers["content-type"] == "application/json"
    assert second.headers["retry-after"] == "0"
    assert second.headers["x-agent-chaos-fault"] == "http_rate_limit"
    assert third.status_code == 200
    assert UPSTREAM_PATHS == ["/customer/123", "/customer/123"]
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_OBSERVED,
        EventType.RETRY_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[3].payload
    failed = recorder.events[4].payload
    assert injected.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": injected.operation_id,
        "fault_type": "http_rate_limit",
        "parameters": {"retry_after_seconds": 0},
    }
    assert failed.model_dump(mode="json") == {
        "kind": "OPERATION_FAILED",
        "operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "failure_kind": "injected_rate_limit",
        "status_code": 429,
        "fault_related": True,
    }
    event_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    for secret in (
        header_secret,
        body_secret,
        query_secret,
        "rate limited by Agent Chaos",
    ):
        assert secret not in event_text


@pytest.mark.asyncio
async def test_proxy_injects_exact_malformed_json_without_contacting_upstream(
    tmp_path: Path,
) -> None:
    fault = HttpMalformedJsonFault.model_validate(
        {
            "type": "http_malformed_json",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 2},
        }
    )
    UPSTREAM_PATHS.clear()
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            first = await client.get("/customer/123")
            injected_response = await client.get("/customer/123")
            retry_response = await client.get("/customer/123")

    assert first.status_code == 200
    assert injected_response.status_code == 200
    assert injected_response.content == b'{"error":"injected by Agent Chaos"'
    assert injected_response.headers["content-type"] == "application/json"
    assert injected_response.headers["x-agent-chaos-fault"] == "http_malformed_json"
    with pytest.raises(json.JSONDecodeError):
        injected_response.json()
    assert retry_response.status_code == 200
    assert UPSTREAM_PATHS == ["/customer/123", "/customer/123"]
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_OBSERVED,
        EventType.RETRY_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[3].payload
    failed = recorder.events[4].payload
    assert injected.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": injected.operation_id,
        "fault_type": "http_malformed_json",
        "parameters": {},
    }
    assert failed.model_dump(mode="json") == {
        "kind": "OPERATION_FAILED",
        "operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "failure_kind": "injected_malformed_json",
        "status_code": 200,
        "fault_related": True,
    }
    assert not any(
        event.event_type == EventType.OPERATION_SUCCEEDED
        and event.payload.operation_id == injected.operation_id
        for event in recorder.events
    )


@pytest.mark.asyncio
async def test_proxy_disconnects_without_contacting_upstream_and_records_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fault = HttpDisconnectFault.model_validate(
        {
            "type": "http_disconnect",
            "target": {"method": "GET", "path": "/customer/*"},
            "trigger": {"occurrence": 2},
        }
    )
    header_secret = "disconnect-header-secret-7a8b"
    body_secret = "disconnect-body-secret-9c0d"
    query_secret = "disconnect-query-secret-1e2f"
    UPSTREAM_PATHS.clear()
    events_at_abort: list[EventType] = []
    original_abort = AgentChaosH11Protocol._abort_transport

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        assert proxy._server is not None
        assert proxy._server.config.http_protocol_class is AgentChaosH11Protocol

        def record_then_abort(protocol: AgentChaosH11Protocol) -> None:
            events_at_abort.extend(event.event_type for event in recorder.events)
            original_abort(protocol)

        monkeypatch.setattr(AgentChaosH11Protocol, "_abort_transport", record_then_abort)
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            request_kwargs = {
                "content": body_secret,
                "headers": {"Authorization": header_secret},
            }
            url = f"/customer/123?token={query_secret}"
            first = await client.request("GET", url, **request_kwargs)
            with pytest.raises(httpx.TransportError):
                await client.request("GET", url, **request_kwargs)
            retry_response = await client.request("GET", url, **request_kwargs)

    assert first.status_code == 200
    assert retry_response.status_code == 200
    assert events_at_abort[-2:] == [EventType.FAULT_INJECTED, EventType.OPERATION_FAILED]
    assert UPSTREAM_PATHS == ["/customer/123", "/customer/123"]
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_FAILED,
        EventType.OPERATION_OBSERVED,
        EventType.RETRY_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[3].payload
    failed = recorder.events[4].payload
    retry = recorder.events[6].payload
    assert injected.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": injected.operation_id,
        "fault_type": "http_disconnect",
        "parameters": {},
    }
    assert failed.model_dump(mode="json") == {
        "kind": "OPERATION_FAILED",
        "operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "failure_kind": "injected_disconnect",
        "status_code": None,
        "fault_related": True,
    }
    assert retry.model_dump(mode="json") == {
        "kind": "RETRY_OBSERVED",
        "operation_id": retry.operation_id,
        "retry_of_operation_id": injected.operation_id,
        "fingerprint": failed.fingerprint,
        "attempt": 2,
    }
    assert not any(
        event.event_type == EventType.OPERATION_SUCCEEDED
        and event.payload.operation_id == injected.operation_id
        for event in recorder.events
    )
    event_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    for secret in (header_secret, body_secret, query_secret, "Authorization"):
        assert secret not in event_text
    assert not any(
        record.name == "uvicorn.error" and record.exc_info is not None for record in caplog.records
    )


@pytest.mark.asyncio
async def test_latency_fault_introduces_delay(tmp_path: Path) -> None:
    fault = HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "latency_ms": 50,
        }
    )
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(
            base_url=proxy.proxy_url, timeout=2, trust_env=False
        ) as client:
            response = await client.get("/customer/123")

    assert response.status_code == 200
    successes = [
        event for event in recorder.events if event.event_type == EventType.OPERATION_SUCCEEDED
    ]
    assert successes[0].payload.duration_ms >= 45
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[1].payload
    assert injected.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": injected.operation_id,
        "fault_type": "http_latency",
        "parameters": {"latency_ms": 50},
    }


@pytest.mark.asyncio
async def test_latency_fault_does_not_forward_after_disconnect(tmp_path: Path) -> None:
    fault = HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "latency_ms": 200,
        }
    )
    UPSTREAM_PATHS.clear()

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(
            base_url=proxy.proxy_url, timeout=0.05, trust_env=False
        ) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.get("/customer/disconnected")
        await asyncio.sleep(0.25)

    assert "/customer/disconnected" not in UPSTREAM_PATHS
    failures = [
        event for event in recorder.events if event.event_type == EventType.OPERATION_FAILED
    ]
    assert failures[0].payload.failure_kind == "client_disconnected"
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_FAILED,
    ]


@pytest.mark.asyncio
async def test_latency_retry_preserves_inferred_timeout_event_order(tmp_path: Path) -> None:
    fault = HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"path": "/customer/*"},
            "trigger": {"occurrence": 1},
            "latency_ms": 250,
        }
    )
    UPSTREAM_PATHS.clear()

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.get("/customer/retry", timeout=0.05)
            retry_response = await client.get("/customer/retry", timeout=2)
        await asyncio.sleep(0.05)

    assert retry_response.status_code == 200
    assert UPSTREAM_PATHS == ["/customer/retry"]
    assert [event.event_type for event in recorder.events] == [
        EventType.OPERATION_OBSERVED,
        EventType.FAULT_INJECTED,
        EventType.OPERATION_OBSERVED,
        EventType.OPERATION_FAILED,
        EventType.RETRY_OBSERVED,
        EventType.OPERATION_SUCCEEDED,
    ]
    injected = recorder.events[1].payload
    inferred_failure = recorder.events[3].payload
    retry = recorder.events[4].payload
    assert inferred_failure.model_dump(mode="json") == {
        "kind": "OPERATION_FAILED",
        "operation_id": injected.operation_id,
        "fingerprint": inferred_failure.fingerprint,
        "failure_kind": "client_timeout_inferred",
        "status_code": None,
        "fault_related": True,
    }
    assert retry.model_dump(mode="json") == {
        "kind": "RETRY_OBSERVED",
        "operation_id": retry.operation_id,
        "retry_of_operation_id": injected.operation_id,
        "fingerprint": inferred_failure.fingerprint,
        "attempt": 2,
    }


@pytest.mark.asyncio
async def test_latency_forwarding_cannot_be_reclassified_as_a_failure(tmp_path: Path) -> None:
    fault = HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"path": "/slow"},
            "trigger": {"occurrence": 1},
            "latency_ms": 10,
        }
    )
    UPSTREAM_PATHS.clear()

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            first_task = asyncio.create_task(client.get("/slow"))
            for _ in range(100):
                if UPSTREAM_PATHS:
                    break
                await asyncio.sleep(0.002)
            assert UPSTREAM_PATHS == ["/slow"]
            second = await client.get("/slow")
            first = await first_task

    assert first.status_code == 200
    assert second.status_code == 200
    assert UPSTREAM_PATHS == ["/slow", "/slow"]
    failures = {
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.OPERATION_FAILED
    }
    successes = {
        event.payload.operation_id
        for event in recorder.events
        if event.event_type == EventType.OPERATION_SUCCEEDED
    }
    assert failures == set()
    assert len(successes) == 2
    assert not any(event.event_type == EventType.RETRY_OBSERVED for event in recorder.events)


@pytest.mark.asyncio
async def test_recovery_fifo_follows_failure_event_order(tmp_path: Path) -> None:
    fault = HttpLatencyFault.model_validate(
        {
            "type": "http_latency",
            "target": {"path": "/customer/*"},
            "trigger": {"occurrences": [1, 2]},
            "latency_ms": 10,
        }
    )
    recorder = EventRecorder(tmp_path / "events.jsonl", "run")
    proxy = ChaosProxy("http://upstream", fault, recorder)
    try:
        first = await proxy._register_fault_operation("injected-first", "fingerprint")
        second = await proxy._register_fault_operation("injected-second", "fingerprint")
        await proxy._emit_failure(second, "client_timeout_inferred", None)
        await proxy._emit_failure(first, "client_timeout_inferred", None)

        first_retry = await proxy._register_retry("retry-one", "fingerprint")
        assert first_retry is second
        await proxy._emit_success(
            OperationSucceededPayload(
                operation_id="retry-one",
                fingerprint="fingerprint",
                status_code=200,
                duration_ms=1,
                fault_related=True,
            ),
            fault_state=None,
            retry_state=first_retry,
        )
        second_retry = await proxy._register_retry("retry-two", "fingerprint")
        assert second_retry is first
    finally:
        await proxy._client.aclose()
        recorder.close()

    retries = [
        event.payload for event in recorder.events if event.event_type == EventType.RETRY_OBSERVED
    ]
    assert [payload.retry_of_operation_id for payload in retries] == [
        "injected-second",
        "injected-first",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_type",
    [
        "http_latency",
        "http_error",
        "http_rate_limit",
        "http_malformed_json",
        "http_disconnect",
    ],
)
async def test_fault_schedule_fires_exactly_under_concurrency(
    tmp_path: Path, fault_type: str
) -> None:
    fault_data = {
        "type": fault_type,
        "target": {"path": "/concurrent"},
        "trigger": {"occurrences": [5, 10]},
    }
    fault: FaultConfig
    if fault_type == "http_latency":
        fault = HttpLatencyFault.model_validate({**fault_data, "latency_ms": 100})
    elif fault_type == "http_error":
        fault = HttpErrorFault.model_validate({**fault_data, "status_code": 503})
    elif fault_type == "http_rate_limit":
        fault = HttpRateLimitFault.model_validate({**fault_data, "retry_after_seconds": 1})
    elif fault_type == "http_malformed_json":
        fault = HttpMalformedJsonFault.model_validate(fault_data)
    else:
        fault = HttpDisconnectFault.model_validate(fault_data)

    UPSTREAM_PATHS.clear()
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            results = await asyncio.gather(
                *(client.get("/concurrent") for _ in range(20)),
                return_exceptions=True,
            )

    responses = [result for result in results if isinstance(result, httpx.Response)]
    errors = [result for result in results if isinstance(result, BaseException)]
    injected = [event for event in recorder.events if event.event_type == EventType.FAULT_INJECTED]
    failures = [
        event
        for event in recorder.events
        if event.event_type == EventType.OPERATION_FAILED and event.payload.fault_related
    ]
    assert len(injected) == 2
    assert all(event.payload.fault_type == fault_type for event in injected)
    assert proxy.trigger is not None
    assert proxy.trigger.count == 20
    assert proxy.trigger.fired is True
    assert proxy.trigger.completed_occurrences == (5, 10)
    assert proxy.trigger.complete is True
    expected_failure_kind = {
        "http_error": "injected_http_error",
        "http_rate_limit": "injected_rate_limit",
        "http_malformed_json": "injected_malformed_json",
        "http_disconnect": "injected_disconnect",
    }.get(fault_type)
    if expected_failure_kind is not None:
        assert [event.payload.failure_kind for event in failures] == [
            expected_failure_kind,
            expected_failure_kind,
        ]
    if fault_type == "http_disconnect":
        assert len(responses) == 18
        assert len(errors) == 2
        assert all(isinstance(error, httpx.TransportError) for error in errors)
        assert all(response.status_code == 200 for response in responses)
        assert UPSTREAM_PATHS == ["/concurrent"] * 18
    else:
        assert errors == []
    if fault_type == "http_error":
        assert sum(response.status_code == 503 for response in responses) == 2
    elif fault_type == "http_rate_limit":
        assert sum(response.status_code == 429 for response in responses) == 2
    elif fault_type == "http_malformed_json":
        assert (
            sum(
                response.headers.get("x-agent-chaos-fault") == "http_malformed_json"
                for response in responses
            )
            == 2
        )


@pytest.mark.asyncio
async def test_forwarding_and_artifacts_exclude_sensitive_http_data(tmp_path: Path) -> None:
    header_secret = "raw-header-secret-9d67"
    body_secret = "raw-body-secret-1f82"
    query_secret = "raw-query-secret-7b31"
    UPSTREAM_REQUESTS.clear()

    async with running_proxy(tmp_path, None) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            response = await client.post(
                f"/echo?token={query_secret}",
                content=body_secret,
                headers={
                    "Authorization": header_secret,
                    "Host": "request-host-must-not-forward",
                    "TE": "trailers",
                    "X-Test": "permitted-header",
                },
            )
        await recorder.emit(
            "workload",
            WorkloadCompletedPayload(
                exit_code=0,
                timed_out=False,
                interrupted=False,
                duration_ms=1,
            ),
        )
        scenario = Scenario.model_validate(
            {
                "schema_version": 1,
                "name": "safe-http-artifacts",
                "dependency": {"type": "http", "base_url": "http://upstream"},
                "workload": {"command": ["python", "agent.py"]},
                "success": {"exit_code": 0},
            }
        )
        report = _build_report(
            "run",
            scenario,
            analyze(scenario, recorder.events),
            recorder.events,
            managed_dependency=False,
        )

    assert response.status_code == 200
    assert response.headers["x-upstream"] == "preserved"
    assert "connection" not in response.headers
    assert UPSTREAM_REQUESTS == [
        {
            "method": "POST",
            "path": "/echo",
            "query": f"token={query_secret}",
            "body": body_secret,
            "authorization": header_secret,
            "host": "upstream",
            "te": "",
            "x-test": "permitted-header",
        }
    ]

    event_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    report_text = report.model_dump_json()
    for secret in (header_secret, body_secret, query_secret, "request-host-must-not-forward"):
        assert secret not in event_text
        assert secret not in report_text
    assert hashlib.sha256(body_secret.encode()).hexdigest() in event_text
    assert hashlib.sha256(f"token={query_secret}".encode()).hexdigest() in event_text


@pytest.mark.asyncio
async def test_malformed_json_events_and_report_exclude_http_payload_data(tmp_path: Path) -> None:
    header_secret = "malformed-header-secret-1a2b"
    body_secret = "malformed-body-secret-3c4d"
    query_secret = "malformed-query-secret-5e6f"
    fault = HttpMalformedJsonFault.model_validate(
        {
            "type": "http_malformed_json",
            "target": {"method": "POST", "path": "/echo"},
            "trigger": {"occurrence": 1},
        }
    )
    UPSTREAM_REQUESTS.clear()

    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            response = await client.post(
                f"/echo?token={query_secret}",
                content=body_secret,
                headers={"Authorization": header_secret},
            )
        await recorder.emit(
            "workload",
            WorkloadCompletedPayload(
                exit_code=0,
                timed_out=False,
                interrupted=False,
                duration_ms=1,
            ),
        )
        scenario = Scenario.model_validate(
            {
                "schema_version": 1,
                "name": "safe-malformed-json-artifacts",
                "dependency": {"type": "http", "base_url": "http://upstream"},
                "workload": {"command": ["python", "agent.py"]},
                "fault": fault.model_dump(mode="json"),
                "success": {"exit_code": 0},
            }
        )
        report = _build_report(
            "run",
            scenario,
            analyze(scenario, recorder.events),
            recorder.events,
            managed_dependency=False,
        )

    assert response.status_code == 200
    assert UPSTREAM_REQUESTS == []
    event_text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    report_text = report.model_dump_json()
    forbidden = (
        header_secret,
        body_secret,
        query_secret,
        '{"error":"injected by Agent Chaos"',
        "X-Agent-Chaos-Fault",
        "Content-Type",
        "Authorization",
    )
    for value in forbidden:
        assert value not in event_text
        assert value not in report_text


@pytest.mark.asyncio
async def test_request_body_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_module, "MAX_BODY_BYTES", 4)
    async with running_proxy(tmp_path, None) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            response = await client.post("/echo", content=b"12345")

    assert response.status_code == 413
    assert any(event.event_type == EventType.OPERATION_FAILED for event in recorder.events)


@pytest.mark.asyncio
async def test_response_body_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_module, "MAX_BODY_BYTES", 4)
    async with running_proxy(tmp_path, None) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            response = await client.get("/large")

    assert response.status_code == 502
    assert any(event.event_type == EventType.OPERATION_FAILED for event in recorder.events)
