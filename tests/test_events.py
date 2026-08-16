from pathlib import Path

import pytest

from agentchaos.events.models import (
    Event,
    FaultInjectedPayload,
    OperationObservedPayload,
    RunStartedPayload,
)
from agentchaos.events.recorder import EventRecorder
from agentchaos.runtime.orchestrator import _safe_url_for_evidence


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


def test_fault_event_accepts_http_malformed_json_without_response_data() -> None:
    payload = FaultInjectedPayload(
        operation_id="operation-1",
        fault_type="http_malformed_json",
        parameters={},
    )

    assert payload.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": "operation-1",
        "fault_type": "http_malformed_json",
        "parameters": {},
    }


def test_fault_event_accepts_http_disconnect_without_response_data() -> None:
    payload = FaultInjectedPayload(
        operation_id="operation-1",
        fault_type="http_disconnect",
        parameters={},
    )

    assert payload.model_dump(mode="json") == {
        "kind": "FAULT_INJECTED",
        "operation_id": "operation-1",
        "fault_type": "http_disconnect",
        "parameters": {},
    }


@pytest.mark.parametrize(
    ("configured", "safe"),
    [
        (
            "http://alice:super-secret@127.0.0.1:9000/root?token=raw-query-secret#fragment",
            "http://127.0.0.1:9000/root",
        ),
        (
            "http://user:password@[::1]:9000/root?credential=value",
            "http://[::1]:9000/root",
        ),
    ],
)
def test_lifecycle_event_urls_exclude_credentials_and_queries(configured: str, safe: str) -> None:
    captured = _safe_url_for_evidence(configured)

    assert captured == safe
    for secret in ("alice", "super-secret", "raw-query-secret", "user", "password", "value"):
        assert secret not in captured
