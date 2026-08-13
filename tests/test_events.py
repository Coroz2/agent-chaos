from pathlib import Path

import pytest

from agentchaos.events.models import Event, OperationObservedPayload, RunStartedPayload
from agentchaos.events.recorder import EventRecorder


@pytest.mark.asyncio
async def test_events_are_ordered_flushed_and_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    recorder = EventRecorder(path, "run-1")

    await recorder.emit("orchestrator", RunStartedPayload(scenario_name="test"))
    await recorder.emit(
        "proxy",
        OperationObservedPayload(
            operation_id="operation-1",
            method="POST",
            path="/customer/123",
            query_hash="query-hash",
            body_hash="body-hash",
            fingerprint="fingerprint",
        ),
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = [Event.model_validate_json(line) for line in lines]
    recorder.close()

    assert [event.sequence for event in parsed] == [1, 2]
    assert parsed[1].payload.path == "/customer/123"
    assert "secret" not in lines[1]
    assert len(recorder.events) == 2


@pytest.mark.asyncio
async def test_event_listener_receives_persisted_event(tmp_path: Path) -> None:
    received: list[Event] = []
    recorder = EventRecorder(tmp_path / "events.jsonl", "run-1", listener=received.append)

    event = await recorder.emit("orchestrator", RunStartedPayload(scenario_name="test"))
    recorder.close()

    assert received == [event]
