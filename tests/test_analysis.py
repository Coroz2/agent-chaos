from collections.abc import Iterable

import pytest

from agentchaos.analysis.analyzer import ExperimentResult, analyze
from agentchaos.config.models import Scenario
from agentchaos.events.models import (
    Event,
    EventPayload,
    FaultInjectedPayload,
    OperationFailedPayload,
    OperationSucceededPayload,
    RetryObservedPayload,
    RunErrorPayload,
    WorkloadCompletedPayload,
)


def scenario(with_fault: bool = True) -> Scenario:
    fault = {
        "type": "http_error",
        "target": {"method": "GET", "path": "/customer/*"},
        "trigger": {"occurrence": 1},
        "status_code": 503,
    }
    return Scenario.model_validate(
        {
            "schema_version": 1,
            "name": "analysis",
            "dependency": {"type": "http", "base_url": "http://localhost:9000"},
            "workload": {"command": ["python", "agent.py"]},
            "fault": fault if with_fault else None,
            "success": {"exit_code": 0},
        }
    )


def events(payloads: Iterable[EventPayload]) -> tuple[Event, ...]:
    return tuple(
        Event(
            event_id=f"event-{index}",
            run_id="run",
            sequence=index,
            timestamp="2026-01-01T00:00:00Z",
            elapsed_ms=index * 10,
            type=payload.kind,
            component="test",
            payload=payload,
        )
        for index, payload in enumerate(payloads, start=1)
    )


@pytest.mark.parametrize(
    ("configured_fault", "payloads", "expected_result", "reason"),
    [
        (
            False,
            [
                WorkloadCompletedPayload(
                    exit_code=0, timed_out=False, interrupted=False, duration_ms=1
                )
            ],
            ExperimentResult.PASSED,
            "BASELINE_SUCCEEDED",
        ),
        (
            True,
            [
                WorkloadCompletedPayload(
                    exit_code=0, timed_out=False, interrupted=False, duration_ms=1
                )
            ],
            ExperimentResult.FAILED,
            "FAULT_NOT_TRIGGERED",
        ),
        (
            True,
            [
                FaultInjectedPayload(
                    operation_id="one", fault_type="http_latency", parameters={"latency_ms": 10}
                ),
                OperationSucceededPayload(
                    operation_id="one",
                    fingerprint="fp",
                    status_code=200,
                    duration_ms=10,
                    fault_related=True,
                ),
                WorkloadCompletedPayload(
                    exit_code=0, timed_out=False, interrupted=False, duration_ms=1
                ),
            ],
            ExperimentResult.PASSED,
            "FAULT_TOLERATED",
        ),
        (
            True,
            [
                FaultInjectedPayload(
                    operation_id="one", fault_type="http_error", parameters={"status_code": 503}
                ),
                OperationFailedPayload(
                    operation_id="one",
                    fingerprint="fp",
                    failure_kind="injected_http_error",
                    status_code=503,
                    fault_related=True,
                ),
                RetryObservedPayload(
                    operation_id="two",
                    retry_of_operation_id="one",
                    fingerprint="fp",
                    attempt=2,
                ),
                OperationSucceededPayload(
                    operation_id="two",
                    fingerprint="fp",
                    status_code=200,
                    duration_ms=1,
                    fault_related=True,
                ),
                WorkloadCompletedPayload(
                    exit_code=0, timed_out=False, interrupted=False, duration_ms=1
                ),
            ],
            ExperimentResult.RECOVERED,
            "RECOVERY_OBSERVED",
        ),
        (
            True,
            [
                FaultInjectedPayload(
                    operation_id="one", fault_type="http_error", parameters={"status_code": 503}
                ),
                OperationFailedPayload(
                    operation_id="one",
                    fingerprint="fp",
                    failure_kind="injected_http_error",
                    status_code=503,
                    fault_related=True,
                ),
                WorkloadCompletedPayload(
                    exit_code=0, timed_out=False, interrupted=False, duration_ms=1
                ),
            ],
            ExperimentResult.FAILED,
            "RECOVERY_NOT_OBSERVED",
        ),
        (
            True,
            [
                WorkloadCompletedPayload(
                    exit_code=-15,
                    timed_out=True,
                    interrupted=False,
                    duration_ms=1000,
                    error="process exceeded timeout",
                )
            ],
            ExperimentResult.FAILED,
            "WORKLOAD_TIMED_OUT",
        ),
        (
            True,
            [
                WorkloadCompletedPayload(
                    exit_code=-15,
                    timed_out=False,
                    interrupted=True,
                    duration_ms=100,
                    error="process interrupted",
                )
            ],
            ExperimentResult.FAILED,
            "INTERRUPTED",
        ),
        (
            True,
            [RunErrorPayload(reason_code="PROXY_START_FAILED", message="bind failed")],
            ExperimentResult.FAILED,
            "PROXY_START_FAILED",
        ),
    ],
)
def test_outcome_table(
    configured_fault: bool,
    payloads: list[EventPayload],
    expected_result: ExperimentResult,
    reason: str,
) -> None:
    result = analyze(scenario(configured_fault), events(payloads))

    assert result.result == expected_result
    assert result.reason_code == reason


def test_workload_failure_takes_precedence() -> None:
    result = analyze(
        scenario(),
        events(
            [
                WorkloadCompletedPayload(
                    exit_code=1, timed_out=False, interrupted=False, duration_ms=1
                )
            ]
        ),
    )

    assert result.result == ExperimentResult.FAILED
    assert result.reason_code == "WORKLOAD_EXIT_CODE_MISMATCH"


def test_recovery_metrics_and_identifiers_remain_unchanged() -> None:
    result = analyze(
        scenario(),
        events(
            [
                FaultInjectedPayload(
                    operation_id="failed-operation",
                    fault_type="http_error",
                    parameters={"status_code": 503},
                ),
                OperationFailedPayload(
                    operation_id="failed-operation",
                    fingerprint="fingerprint",
                    failure_kind="injected_http_error",
                    status_code=503,
                    fault_related=True,
                ),
                RetryObservedPayload(
                    operation_id="retry-operation",
                    retry_of_operation_id="failed-operation",
                    fingerprint="fingerprint",
                    attempt=2,
                ),
                OperationSucceededPayload(
                    operation_id="retry-operation",
                    fingerprint="fingerprint",
                    status_code=200,
                    duration_ms=1,
                    fault_related=True,
                ),
                WorkloadCompletedPayload(
                    exit_code=0,
                    timed_out=False,
                    interrupted=False,
                    duration_ms=1,
                ),
            ]
        ),
    )

    assert result.result == ExperimentResult.RECOVERED
    assert result.reason_code == "RECOVERY_OBSERVED"
    assert result.faults_injected == 1
    assert result.successful_operations == 1
    assert result.failed_operations == 1
    assert result.retries_observed == 1
    assert result.recovery_observed is True
    assert result.failed_operation_id == "failed-operation"
    assert result.retry_operation_id == "retry-operation"
    assert result.recovery_latency_ms == 20
