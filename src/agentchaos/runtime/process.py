"""POSIX subprocess lifecycle and log capture."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    stdout_path: Path
    stderr_path: Path
    label: str
    stream_output: bool = True


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    interrupted: bool
    duration_ms: int
    error: str | None = None


class ManagedProcess:
    """A subprocess started in its own process group with captured output."""

    def __init__(self, spec: ProcessSpec) -> None:
        self.spec = spec
        self.process: asyncio.subprocess.Process | None = None
        self._pump_tasks: list[asyncio.Task[None]] = []
        self._started: float | None = None

    @property
    def pid(self) -> int:
        if self.process is None or self.process.pid is None:
            raise RuntimeError("process has not started")
        return self.process.pid

    @property
    def returncode(self) -> int | None:
        return None if self.process is None else self.process.returncode

    async def start(self) -> None:
        self.spec.stdout_path.parent.mkdir(parents=True, exist_ok=True)
        self.spec.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            *self.spec.command,
            cwd=self.spec.cwd,
            env=self.spec.env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._started = time.monotonic()
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._pump_tasks = [
            asyncio.create_task(
                self._pump(
                    self.process.stdout,
                    self.spec.stdout_path,
                    sys.stdout.buffer,
                    "OUT",
                )
            ),
            asyncio.create_task(
                self._pump(
                    self.process.stderr,
                    self.spec.stderr_path,
                    sys.stderr.buffer,
                    "ERR",
                )
            ),
        ]

    async def wait(self, timeout_seconds: float | None = None) -> ProcessResult:
        if self.process is None or self._started is None:
            raise RuntimeError("process has not started")

        timed_out = False
        interrupted = False
        error: str | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                await self.process.wait()
        except TimeoutError:
            timed_out = True
            error = f"process exceeded {timeout_seconds:g} second timeout"
            await self.stop()
        except asyncio.CancelledError:
            interrupted = True
            error = "process interrupted"
            await self.stop()

        await self._finish_pumps()
        return ProcessResult(
            exit_code=self.process.returncode,
            timed_out=timed_out,
            interrupted=interrupted,
            duration_ms=round((time.monotonic() - self._started) * 1000),
            error=error,
        )

    async def stop(self, grace_seconds: float = 2.0) -> None:
        if self.process is None or self.process.returncode is not None:
            await self._finish_pumps()
            return

        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            async with asyncio.timeout(grace_seconds):
                await self.process.wait()
        except TimeoutError:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
        await self._finish_pumps()

    async def _finish_pumps(self) -> None:
        if self._pump_tasks:
            await asyncio.gather(*self._pump_tasks, return_exceptions=True)
            self._pump_tasks.clear()

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        path: Path,
        terminal: BinaryIO,
        channel: str,
    ) -> None:
        with path.open("wb") as output:
            while chunk := await reader.readline():
                output.write(chunk)
                output.flush()
                if self.spec.stream_output:
                    prefix = f"[{self.spec.label} {channel}] ".encode()
                    terminal.write(prefix + chunk)
                    terminal.flush()


def merged_environment(overrides: dict[str, str]) -> dict[str, str]:
    """Return the parent environment with deterministic scenario overrides."""
    return {**os.environ, **overrides}
