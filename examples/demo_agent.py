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
    backoff_seconds = int(os.environ.get("DEMO_BACKOFF_MS", "50")) / 1000

    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        customer = get_customer(client, base_url)
        print(f"Loaded customer {customer['id']}", flush=True)

        for attempt in range(1, max_attempts + 1):
            try:
                customer = get_customer(client, base_url)
                print(f"Refreshed customer {customer['id']} on attempt {attempt}", flush=True)
                return 0
            except (httpx.TimeoutException, httpx.HTTPStatusError, json.JSONDecodeError) as error:
                print(f"Attempt {attempt} failed: {type(error).__name__}", flush=True)
                if attempt == max_attempts:
                    return 1
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
