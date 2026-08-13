"""Managed HTTP dependency readiness checks."""

from __future__ import annotations

import asyncio
import time

import httpx

from agentchaos.runtime.process import ManagedProcess


class DependencyReadinessError(RuntimeError):
    """Raised when a managed dependency does not become ready."""


async def wait_for_http_readiness(
    process: ManagedProcess,
    url: str,
    timeout_seconds: float,
) -> None:
    """Poll a managed dependency until it returns a successful readiness response."""
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(timeout=0.5, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.returncode is not None:
                raise DependencyReadinessError(
                    f"dependency exited with code {process.returncode} before becoming ready"
                )
            try:
                response = await client.get(url)
                if 200 <= response.status_code < 400:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)
    raise DependencyReadinessError(f"dependency did not become ready within {timeout_seconds:g}s")
