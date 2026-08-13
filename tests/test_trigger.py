import asyncio

import pytest

from agentchaos.faults.trigger import OccurrenceTrigger, path_matches


@pytest.mark.asyncio
async def test_occurrence_trigger_fires_once() -> None:
    trigger = OccurrenceTrigger(2)

    assert await trigger.evaluate() is False
    assert await trigger.evaluate() is True
    assert await trigger.evaluate() is False
    assert trigger.count == 3
    assert trigger.fired is True


@pytest.mark.asyncio
async def test_occurrence_trigger_is_concurrency_safe() -> None:
    trigger = OccurrenceTrigger(25)
    results = await asyncio.gather(*(trigger.evaluate() for _ in range(100)))

    assert sum(results) == 1
    assert trigger.count == 100


def test_path_matching_supports_only_star_semantics() -> None:
    assert path_matches("/customer/*", "/customer/123")
    assert path_matches("/customer/*/orders", "/customer/123/orders")
    assert not path_matches("/customer/*", "/orders/123")
