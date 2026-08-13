import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agentchaos.config.models import HttpErrorFault, HttpLatencyFault
from agentchaos.events.models import EventType
from agentchaos.events.recorder import EventRecorder
from agentchaos.proxy import server as proxy_module
from agentchaos.proxy.server import ChaosProxy

UPSTREAM_PATHS: list[str] = []


async def echo(request: Request) -> JSONResponse:
    UPSTREAM_PATHS.append(request.url.path)
    return JSONResponse(
        {
            "method": request.method,
            "path": request.url.path,
            "header": request.headers.get("x-test"),
        }
    )


async def large_response(request: Request) -> JSONResponse:
    return JSONResponse({"value": "12345"})


UPSTREAM = Starlette(
    routes=[
        Route("/large", large_response),
        Route("/{path:path}", echo, methods=["GET", "POST"]),
    ]
)


@asynccontextmanager
async def running_proxy(
    tmp_path: Path, fault: HttpErrorFault | HttpLatencyFault | None
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
    async with running_proxy(tmp_path, fault) as (proxy, recorder):
        assert proxy.proxy_url is not None
        async with httpx.AsyncClient(base_url=proxy.proxy_url, trust_env=False) as client:
            first = await client.get("/customer/123", headers={"X-Test": "preserved"})
            second = await client.get("/customer/123")
            third = await client.get("/customer/123")

    assert first.json()["header"] == "preserved"
    assert second.status_code == 503
    assert second.headers["x-agent-chaos-fault"] == "http_error"
    assert third.status_code == 200
    assert any(event.event_type == EventType.RETRY_OBSERVED for event in recorder.events)


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
