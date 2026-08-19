"""Small deterministic workload with retry behavior."""

from __future__ import annotations

import json
import os
import sys
import time

import httpx


def get_customer(client: httpx.Client, base_url: str) -> dict[str, object]:
    response = client.get(f"{base_url}/customer/123")
    response.raise_for_status()
    value: dict[str, object] = response.json()
    return value


def main() -> int:
    base_url = os.environ["CUSTOMER_API_URL"].rstrip("/")
    timeout_seconds = int(os.environ.get("DEMO_TIMEOUT_MS", "150")) / 1000
    max_attempts = int(os.environ.get("DEMO_MAX_ATTEMPTS", "3"))
    refreshes = int(os.environ.get("DEMO_REFRESHES", "1"))
    backoff_seconds = int(os.environ.get("DEMO_BACKOFF_MS", "50")) / 1000
    exhausted_exit_code = int(os.environ.get("DEMO_EXHAUSTED_EXIT_CODE", "1"))

    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        customer = get_customer(client, base_url)
        print(f"Loaded customer {customer['id']}", flush=True)

        for refresh in range(1, refreshes + 1):
            for attempt in range(1, max_attempts + 1):
                try:
                    customer = get_customer(client, base_url)
                    if refreshes == 1:
                        message = f"Refreshed customer {customer['id']} on attempt {attempt}"
                    else:
                        message = (
                            f"Refreshed customer {customer['id']} in cycle {refresh} "
                            f"on attempt {attempt}"
                        )
                    print(message, flush=True)
                    break
                except (
                    httpx.TransportError,
                    httpx.HTTPStatusError,
                    json.JSONDecodeError,
                ) as error:
                    print(f"Attempt {attempt} failed: {type(error).__name__}", flush=True)
                    if attempt == max_attempts:
                        return exhausted_exit_code
                    delay_seconds = backoff_seconds * (2 ** (attempt - 1))
                    if (
                        isinstance(error, httpx.HTTPStatusError)
                        and error.response.status_code == 429
                    ):
                        retry_after = error.response.headers.get("Retry-After")
                        try:
                            configured_delay = int(retry_after) if retry_after is not None else -1
                        except ValueError:
                            configured_delay = -1
                        if configured_delay >= 0:
                            delay_seconds = configured_delay
                    time.sleep(delay_seconds)
        return 0


if __name__ == "__main__":
    sys.exit(main())
