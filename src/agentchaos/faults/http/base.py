"""Private HTTP fault-execution contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

HttpFaultType = Literal["http_latency", "http_error", "http_malformed_json"]


class HttpFaultAction(StrEnum):
    """Disposition returned by an injected HTTP fault executor."""

    FORWARD = "forward"
    RESPOND = "respond"
    ABANDON = "abandon"


@dataclass(frozen=True, slots=True)
class SyntheticHttpResponse:
    """Transport-neutral description of a fixed injected response."""

    content: bytes
    status_code: int
    headers: tuple[tuple[str, str], ...]
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class HttpFaultOutcome:
    """Result of executing one selected HTTP fault."""

    action: HttpFaultAction
    response: SyntheticHttpResponse | None = None
    failure_kind: str | None = None
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class HttpFaultExecutionContext:
    """HTTP state required by an executor without exposing proxy transport mechanics."""

    retry_seen: asyncio.Event
    is_disconnected: Callable[[], Awaitable[bool]]


class HttpFaultExecutor(Protocol):
    """Private protocol implemented by each supported HTTP fault effect."""

    @property
    def fault_type(self) -> HttpFaultType: ...

    def event_parameters(self) -> dict[str, int]: ...

    async def execute(self, context: HttpFaultExecutionContext) -> HttpFaultOutcome: ...
