"""Append-only JSON Lines event recorder."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import uuid4

from agentchaos.events.models import Event, EventPayload


class EventRecorder:
    """Assign event order and durably append each event to a run stream."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        listener: Callable[[Event], None] | None = None,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self._started = time.monotonic()
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._events: list[Event] = []
        self._listener = listener
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    async def emit(self, component: str, payload: EventPayload) -> Event:
        async with self._lock:
            self._sequence += 1
            event = Event(
                event_id=str(uuid4()),
                run_id=self.run_id,
                sequence=self._sequence,
                timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                elapsed_ms=max(0, round((time.monotonic() - self._started) * 1000)),
                type=payload.kind,
                component=component,
                payload=payload,
            )
            self._file.write(event.model_dump_json(by_alias=True) + "\n")
            self._file.flush()
            self._events.append(event)
            if self._listener is not None:
                self._listener(event)
            return event

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
