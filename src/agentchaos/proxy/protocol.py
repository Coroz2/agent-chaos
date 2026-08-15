"""Private Uvicorn protocol adapter for active HTTP connection access."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from uvicorn._types import ASGIReceiveCallable, ASGISendCallable, Scope
from uvicorn.config import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

TRANSPORT_ABORT_STATE_KEY = "_agentchaos_abort_transport"
TransportAbort = Callable[[], None]


class AgentChaosH11Protocol(H11Protocol):
    """Expose only the current inbound connection's abort operation to the proxy app."""

    def __init__(
        self,
        config: Config,
        server_state: ServerState,
        app_state: dict[str, Any],
        _loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        super().__init__(config, server_state, app_state, _loop)
        app = self.app

        async def app_with_transport_abort(
            scope: Scope,
            receive: ASGIReceiveCallable,
            send: ASGISendCallable,
        ) -> None:
            if scope["type"] == "http":
                scope["state"][TRANSPORT_ABORT_STATE_KEY] = self._abort_transport
            await app(scope, receive, send)

        self.app = app_with_transport_abort

    def _abort_transport(self) -> None:
        # Prevent the inert response returned by the endpoint from writing after abort().
        if self.cycle is not None:
            self.cycle.disconnected = True
        self.transport.abort()
