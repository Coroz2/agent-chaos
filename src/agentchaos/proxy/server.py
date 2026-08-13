"""Buffered HTTP reverse proxy with deterministic fault injection."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agentchaos.config.models import FaultConfig, HttpErrorFault, HttpLatencyFault
from agentchaos.events.models import (
    FaultInjectedPayload,
    OperationFailedPayload,
    OperationObservedPayload,
    OperationSucceededPayload,
    RetryObservedPayload,
)
from agentchaos.events.recorder import EventRecorder
from agentchaos.faults.trigger import OccurrenceTrigger, path_matches

MAX_BODY_BYTES = 10 * 1024 * 1024
HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
SUPPORTED_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]


@dataclass(slots=True)
class FaultOperationState:
    operation_id: str
    fingerprint: str
    fault_type: str
    status: str = "pending"
    failure_emitted: bool = False
    retry_attempts: int = 1
    retry_seen: asyncio.Event = field(default_factory=asyncio.Event)


class ChaosProxy:
    """Manage an in-process ASGI reverse proxy on an ephemeral loopback port."""

    def __init__(
        self,
        upstream_url: str,
        fault: FaultConfig | None,
        recorder: EventRecorder,
    ) -> None:
        self.upstream_url = upstream_url.rstrip("/")
        self.fault = fault
        self.recorder = recorder
        self.trigger = None if fault is None else OccurrenceTrigger(fault.trigger.occurrence)
        self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._state_lock = asyncio.Lock()
        self._fault_operation: FaultOperationState | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self.proxy_url: str | None = None
        routes = [
            Route("/", self._handle, methods=SUPPORTED_METHODS),
            Route("/{path:path}", self._handle, methods=SUPPORTED_METHODS),
        ]
        self.app = Starlette(routes=routes)

    @property
    def fault_injected(self) -> bool:
        return self.trigger is not None and self.trigger.fired

    async def start(self) -> str:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self._socket.setblocking(False)
        port = int(self._socket.getsockname()[1])
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve(sockets=[self._socket]))
        for _ in range(500):
            if self._server.started:
                self.proxy_url = f"http://127.0.0.1:{port}"
                return self.proxy_url
            if self._server_task.done():
                await self._server_task
                raise RuntimeError("proxy exited during startup")
            await asyncio.sleep(0.01)
        raise RuntimeError("proxy did not become ready")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            await self._server_task
        await self._client.aclose()

    async def _handle(self, request: Request) -> Response:
        started = time.monotonic()
        body = await self._read_request_body(request)
        operation_id = str(uuid4())
        path = request.url.path
        query = request.scope.get("query_string", b"")
        query_hash = hashlib.sha256(query).hexdigest()
        body_hash = hashlib.sha256(body or b"").hexdigest()
        fingerprint = self._fingerprint(request.method, path, query_hash, body_hash)
        await self.recorder.emit(
            "proxy",
            OperationObservedPayload(
                operation_id=operation_id,
                method=request.method,
                path=path,
                query_hash=query_hash,
                body_hash=body_hash,
                fingerprint=fingerprint,
            ),
        )

        retry_state, emit_inferred_failure = await self._register_retry(operation_id, fingerprint)
        if emit_inferred_failure and retry_state is not None:
            await self._emit_failure(
                retry_state,
                failure_kind="client_timeout_inferred",
                status_code=None,
            )
        if retry_state is not None:
            await self.recorder.emit(
                "proxy",
                RetryObservedPayload(
                    operation_id=operation_id,
                    retry_of_operation_id=retry_state.operation_id,
                    fingerprint=fingerprint,
                    attempt=retry_state.retry_attempts,
                ),
            )

        if body is None:
            await self._emit_plain_failure(
                operation_id, fingerprint, "request_body_too_large", 413, False
            )
            return JSONResponse({"error": "request body exceeds proxy limit"}, status_code=413)

        inject = await self._should_inject(request.method, path)
        fault_state: FaultOperationState | None = None
        if inject:
            assert self.fault is not None
            fault_state = FaultOperationState(operation_id, fingerprint, self.fault.type)
            async with self._state_lock:
                self._fault_operation = fault_state
            await self.recorder.emit(
                "proxy",
                FaultInjectedPayload(
                    operation_id=operation_id,
                    fault_type=self.fault.type,
                    parameters=self._fault_parameters(self.fault),
                ),
            )
            if isinstance(self.fault, HttpErrorFault):
                fault_state.status = "failed"
                await self._emit_failure(
                    fault_state,
                    failure_kind="injected_http_error",
                    status_code=self.fault.status_code,
                )
                return JSONResponse(
                    {"error": "injected by Agent Chaos"},
                    status_code=self.fault.status_code,
                    headers={"X-Agent-Chaos-Fault": "http_error"},
                )
            if isinstance(self.fault, HttpLatencyFault):
                abandoned = await self._apply_latency(request, self.fault, fault_state)
                if abandoned:
                    if not fault_state.failure_emitted:
                        await self._emit_failure(
                            fault_state,
                            failure_kind="client_disconnected",
                            status_code=None,
                        )
                    return Response(status_code=499)

        response = await self._forward(request, body)
        duration_ms = round((time.monotonic() - started) * 1000)
        if response.status_code < 400:
            if fault_state is not None:
                fault_state.status = "succeeded"
            await self.recorder.emit(
                "proxy",
                OperationSucceededPayload(
                    operation_id=operation_id,
                    fingerprint=fingerprint,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    fault_related=fault_state is not None or retry_state is not None,
                ),
            )
        else:
            failure_kind = "upstream_http_error" if response.status_code != 502 else "proxy_error"
            if fault_state is not None:
                fault_state.status = "failed"
                await self._emit_failure(fault_state, failure_kind, response.status_code)
            else:
                await self._emit_plain_failure(
                    operation_id,
                    fingerprint,
                    failure_kind,
                    response.status_code,
                    False,
                )
        return response

    async def _register_retry(
        self, operation_id: str, fingerprint: str
    ) -> tuple[FaultOperationState | None, bool]:
        async with self._state_lock:
            state = self._fault_operation
            if (
                state is None
                or state.operation_id == operation_id
                or state.fingerprint != fingerprint
                or state.status == "succeeded"
            ):
                return None, False
            emit_inferred_failure = state.status == "pending" and not state.failure_emitted
            if state.status == "pending":
                state.status = "failed"
            state.retry_attempts += 1
            state.retry_seen.set()
            return state, emit_inferred_failure

    async def _apply_latency(
        self,
        request: Request,
        fault: HttpLatencyFault,
        state: FaultOperationState,
    ) -> bool:
        delay_task = asyncio.create_task(asyncio.sleep(fault.latency_ms / 1000))
        disconnect_task = asyncio.create_task(self._wait_for_disconnect(request))
        retry_task = asyncio.create_task(state.retry_seen.wait())
        done, pending = await asyncio.wait(
            {delay_task, disconnect_task, retry_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return disconnect_task in done or retry_task in done

    async def _wait_for_disconnect(self, request: Request) -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(0.01)

    async def _should_inject(self, method: str, path: str) -> bool:
        if self.fault is None or self.trigger is None:
            return False
        target = self.fault.target
        if target.method is not None and target.method != method.upper():
            return False
        if not path_matches(target.path, path):
            return False
        return await self.trigger.evaluate()

    async def _forward(self, request: Request, body: bytes) -> Response:
        headers = [
            (name, value)
            for name, value in request.headers.raw
            if name.lower() not in HOP_BY_HOP_HEADERS | {b"host", b"content-length"}
        ]
        url = self.upstream_url + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        try:
            async with self._client.stream(
                request.method, url, headers=headers, content=body
            ) as upstream:
                content = bytearray()
                async for chunk in upstream.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BODY_BYTES:
                        return JSONResponse(
                            {"error": "upstream body exceeds proxy limit"}, status_code=502
                        )
                status_code = upstream.status_code
                response_headers = {
                    name: value
                    for name, value in upstream.headers.items()
                    if name.lower().encode() not in HOP_BY_HOP_HEADERS | {b"content-length"}
                }
        except httpx.HTTPError as error:
            return JSONResponse(
                {"error": "upstream request failed", "detail": type(error).__name__},
                status_code=502,
            )
        return Response(
            content=bytes(content),
            status_code=status_code,
            headers=response_headers,
        )

    @staticmethod
    async def _read_request_body(request: Request) -> bytes | None:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_BODY_BYTES:
                return None
        return bytes(content)

    async def _emit_failure(
        self,
        state: FaultOperationState,
        failure_kind: str,
        status_code: int | None,
    ) -> None:
        if state.failure_emitted:
            return
        state.failure_emitted = True
        await self._emit_plain_failure(
            state.operation_id,
            state.fingerprint,
            failure_kind,
            status_code,
            True,
        )

    async def _emit_plain_failure(
        self,
        operation_id: str,
        fingerprint: str,
        failure_kind: str,
        status_code: int | None,
        fault_related: bool,
    ) -> None:
        await self.recorder.emit(
            "proxy",
            OperationFailedPayload(
                operation_id=operation_id,
                fingerprint=fingerprint,
                failure_kind=failure_kind,
                status_code=status_code,
                fault_related=fault_related,
            ),
        )

    @staticmethod
    def _fingerprint(method: str, path: str, query_hash: str, body_hash: str) -> str:
        value = "\0".join((method.upper(), path, query_hash, body_hash)).encode()
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _fault_parameters(fault: FaultConfig) -> dict[str, int]:
        if isinstance(fault, HttpLatencyFault):
            return {"latency_ms": fault.latency_ms}
        return {"status_code": fault.status_code}
