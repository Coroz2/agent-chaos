"""Buffered HTTP reverse proxy with deterministic fault injection."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from typing import cast
from uuid import uuid4

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agentchaos.config.models import FaultConfig
from agentchaos.events.models import (
    FaultInjectedPayload,
    OperationFailedPayload,
    OperationObservedPayload,
    OperationSucceededPayload,
    RetryObservedPayload,
)
from agentchaos.events.recorder import EventRecorder
from agentchaos.faults.http import (
    HttpFaultAction,
    HttpFaultExecutionContext,
    HttpFaultExecutor,
    HttpTargetMatcher,
    build_http_fault_executor,
)
from agentchaos.faults.trigger import OccurrenceTrigger
from agentchaos.proxy.protocol import (
    TRANSPORT_ABORT_STATE_KEY,
    AgentChaosH11Protocol,
    TransportAbort,
)

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
        self.trigger = None if fault is None else OccurrenceTrigger(fault.trigger.schedule)
        self._target_matcher = (
            None if fault is None else HttpTargetMatcher.from_config(fault.target)
        )
        self._fault_executor: HttpFaultExecutor | None = (
            None if fault is None else build_http_fault_executor(fault)
        )
        self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._selection_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._fault_operations: dict[str, deque[FaultOperationState]] = {}
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
            http=AgentChaosH11Protocol,
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
        retry_state, fault_state = await self._begin_operation(
            operation_id=operation_id,
            method=request.method,
            path=path,
            query_hash=query_hash,
            body_hash=body_hash,
            fingerprint=fingerprint,
            can_inject=body is not None,
        )

        if body is None:
            await self._emit_plain_failure(
                operation_id, fingerprint, "request_body_too_large", 413, False
            )
            return JSONResponse({"error": "request body exceeds proxy limit"}, status_code=413)

        if fault_state is not None:
            executor = self._fault_executor
            assert executor is not None
            injected_response = await self._execute_fault(request, executor, fault_state)
            if injected_response is not None:
                return injected_response

        response = await self._forward(request, body)
        duration_ms = round((time.monotonic() - started) * 1000)
        if response.status_code < 400:
            await self._emit_success(
                OperationSucceededPayload(
                    operation_id=operation_id,
                    fingerprint=fingerprint,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                    fault_related=fault_state is not None or retry_state is not None,
                ),
                fault_state=fault_state,
                retry_state=retry_state,
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

    async def _begin_operation(
        self,
        *,
        operation_id: str,
        method: str,
        path: str,
        query_hash: str,
        body_hash: str,
        fingerprint: str,
        can_inject: bool,
    ) -> tuple[FaultOperationState | None, FaultOperationState | None]:
        observed = OperationObservedPayload(
            operation_id=operation_id,
            method=method,
            path=path,
            query_hash=query_hash,
            body_hash=body_hash,
            fingerprint=fingerprint,
        )
        matching = (
            self._target_matcher is not None
            and self.trigger is not None
            and self._target_matcher.matches(method, path)
        )
        if matching:
            assert self.trigger is not None
            async with self._selection_lock:
                await self.recorder.emit("proxy", observed)
                retry_state = await self._register_retry(operation_id, fingerprint)
                inject = can_inject and await self.trigger.evaluate()
                fault_state = (
                    await self._register_fault_operation(operation_id, fingerprint)
                    if inject
                    else None
                )
                return retry_state, fault_state

        await self.recorder.emit("proxy", observed)
        retry_state = await self._register_retry(operation_id, fingerprint)
        return retry_state, None

    async def _register_retry(
        self, operation_id: str, fingerprint: str
    ) -> FaultOperationState | None:
        async with self._state_lock:
            queue = self._fault_operations.get(fingerprint)
            if not queue:
                return None
            state = queue[0]
            if state.status == "pending":
                await self._emit_failure_locked(
                    state,
                    failure_kind="client_timeout_inferred",
                    status_code=None,
                )
            state.retry_attempts += 1
            state.retry_seen.set()
            await self.recorder.emit(
                "proxy",
                RetryObservedPayload(
                    operation_id=operation_id,
                    retry_of_operation_id=state.operation_id,
                    fingerprint=fingerprint,
                    attempt=state.retry_attempts,
                ),
            )
            return state

    async def _register_fault_operation(
        self, operation_id: str, fingerprint: str
    ) -> FaultOperationState:
        executor = self._fault_executor
        assert executor is not None
        state = FaultOperationState(operation_id, fingerprint, executor.fault_type)
        async with self._state_lock:
            self._fault_operations.setdefault(fingerprint, deque()).append(state)
            await self.recorder.emit(
                "proxy",
                FaultInjectedPayload(
                    operation_id=operation_id,
                    fault_type=executor.fault_type,
                    parameters=executor.event_parameters(),
                ),
            )
        return state

    async def _emit_success(
        self,
        payload: OperationSucceededPayload,
        *,
        fault_state: FaultOperationState | None,
        retry_state: FaultOperationState | None,
    ) -> None:
        if fault_state is None and retry_state is None:
            await self.recorder.emit("proxy", payload)
            return
        async with self._state_lock:
            if fault_state is not None:
                fault_state.status = "succeeded"
                self._remove_fault_operation_locked(fault_state)
            await self.recorder.emit("proxy", payload)
            if retry_state is not None:
                retry_state.status = "succeeded"
                self._remove_fault_operation_locked(retry_state)

    def _remove_fault_operation_locked(self, state: FaultOperationState) -> None:
        queue = self._fault_operations.get(state.fingerprint)
        if queue is None:
            return
        try:
            queue.remove(state)
        except ValueError:
            return
        if not queue:
            del self._fault_operations[state.fingerprint]

    async def _execute_fault(
        self,
        request: Request,
        executor: HttpFaultExecutor,
        state: FaultOperationState,
    ) -> Response | None:
        outcome = await executor.execute(
            HttpFaultExecutionContext(
                retry_seen=state.retry_seen,
                is_disconnected=request.is_disconnected,
            )
        )
        if outcome.action == HttpFaultAction.FORWARD:
            return None
        if outcome.action == HttpFaultAction.ABANDON:
            if not state.failure_emitted:
                assert outcome.failure_kind is not None
                await self._emit_failure(
                    state,
                    failure_kind=outcome.failure_kind,
                    status_code=outcome.status_code,
                )
            return Response(status_code=499)
        if outcome.action == HttpFaultAction.DISCONNECT:
            assert outcome.failure_kind is not None
            state.status = "failed"
            await self._emit_failure(
                state,
                failure_kind=outcome.failure_kind,
                status_code=outcome.status_code,
            )
            abort_transport = request.scope["state"].get(TRANSPORT_ABORT_STATE_KEY)
            if not callable(abort_transport):
                raise RuntimeError("active HTTP transport abort callback is unavailable")
            cast(TransportAbort, abort_transport)()
            return Response(status_code=204)

        response = outcome.response
        assert response is not None
        assert outcome.failure_kind is not None
        state.status = "failed"
        await self._emit_failure(
            state,
            failure_kind=outcome.failure_kind,
            status_code=outcome.status_code,
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

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
        async with self._state_lock:
            await self._emit_failure_locked(state, failure_kind, status_code)

    async def _emit_failure_locked(
        self,
        state: FaultOperationState,
        failure_kind: str,
        status_code: int | None,
    ) -> None:
        if state.failure_emitted:
            return
        state.status = "failed"
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
