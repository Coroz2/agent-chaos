import asyncio
from decimal import Decimal

import pytest

from agentchaos.faults.trigger import (
    OccurrenceTrigger,
    ProbabilityTrigger,
    path_matches,
    probability_selects,
)


@pytest.mark.asyncio
async def test_occurrence_trigger_fires_once() -> None:
    trigger = OccurrenceTrigger(2)

    assert await trigger.evaluate() is False
    assert await trigger.evaluate() is True
    assert await trigger.evaluate() is False
    assert trigger.count == 3
    assert trigger.fired is True


@pytest.mark.asyncio
async def test_occurrence_trigger_selects_each_scheduled_ordinal() -> None:
    trigger = OccurrenceTrigger((2, 4))

    results = [await trigger.evaluate() for _ in range(6)]

    assert results == [False, True, False, True, False, False]
    assert trigger.completed_occurrences == (2, 4)
    assert trigger.complete is True


@pytest.mark.asyncio
async def test_occurrence_trigger_is_concurrency_safe() -> None:
    trigger = OccurrenceTrigger(25)
    results = await asyncio.gather(*(trigger.evaluate() for _ in range(100)))

    assert sum(results) == 1
    assert trigger.count == 100


def test_probability_selector_has_portable_golden_vector() -> None:
    selected = [
        occurrence
        for occurrence in range(1, 7)
        if probability_selects(Decimal("0.5"), 10, occurrence)
    ]

    assert selected == [2, 4]


@pytest.mark.asyncio
async def test_probability_trigger_evaluates_only_the_inclusive_window() -> None:
    trigger = ProbabilityTrigger(Decimal("0.5"), 10, 2, 5)

    results = [await trigger.evaluate() for _ in range(7)]

    assert results == [False, True, False, True, False, False, False]
    assert trigger.evaluated_occurrences == 4
    assert trigger.selected_occurrences == (2, 4)
    assert trigger.complete is True


@pytest.mark.asyncio
async def test_probability_one_selects_every_window_occurrence() -> None:
    trigger = ProbabilityTrigger(Decimal("1.000000"), 0, 2, 4)

    results = [await trigger.evaluate() for _ in range(5)]

    assert results == [False, True, True, True, False]


@pytest.mark.asyncio
async def test_probability_trigger_is_concurrency_safe() -> None:
    trigger = ProbabilityTrigger(Decimal("1"), 42, 5, 10)
    results = await asyncio.gather(*(trigger.evaluate() for _ in range(20)))

    assert sum(results) == 6
    assert trigger.evaluated_occurrences == 6
    assert trigger.selected_occurrences == (5, 6, 7, 8, 9, 10)


def test_path_matching_supports_only_star_semantics() -> None:
    assert path_matches("/customer/*", "/customer/123")
    assert path_matches("/customer/*/orders", "/customer/123/orders")
    assert not path_matches("/customer/*", "/orders/123")
