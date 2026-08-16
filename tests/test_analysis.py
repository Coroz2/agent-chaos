from collections.abc import Iterable
from unittest.mock import patch

import pytest

from agentchaos.analysis.analyzer import AnalysisResult, ExperimentResult, analyze
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


def rate_limit_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "schema_version": 1,
            "name": "rate-limit-analysis",
            "dependency": {"type": "http", "base_url": "http://localhost:9000"},
            "workload": {"command": ["python", "agent.py"]},
            "fault": {
                "type": "http_rate_limit",
                "target": {"method": "GET", "path": "/customer/*"},
                "trigger": {"occurrence": 1},
                "retry_after_seconds": 1,
            },
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


def analyze_schedule(schedule: tuple[int, ...], payloads: Iterable[EventPayload]) -> AnalysisResult:
    with patch(
        "agentchaos.analysis.analyzer._configured_schedule",
        return_value=schedule,
    ):
        return analyze(scenario(), events(payloads))


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
            [],
            ExperimentResult.FAILED,
            "WORKLOAD_NOT_COMPLETED",
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


@pytest.mark.parametrize("successful_retry", [True, False])
def test_rate_limit_recovery_requires_successful_fingerprint_matched_retry(
    successful_retry: bool,
) -> None:
    payloads: list[EventPayload] = [
        FaultInjectedPayload(
            operation_id="failed-operation",
            fault_type="http_rate_limit",
            parameters={"retry_after_seconds": 1},
        ),
        OperationFailedPayload(
            operation_id="failed-operation",
            fingerprint="fingerprint",
            failure_kind="injected_rate_limit",
            status_code=429,
            fault_related=True,
        ),
    ]
    if successful_retry:
        payloads.extend(
            [
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
            ]
        )
    payloads.append(
        WorkloadCompletedPayload(
            exit_code=0,
            timed_out=False,
            interrupted=False,
            duration_ms=1,
        )
    )

    result = analyze(rate_limit_scenario(), events(payloads))

    assert result.result == (
        ExperimentResult.RECOVERED if successful_retry else ExperimentResult.FAILED
    )
    assert result.reason_code == (
        "RECOVERY_OBSERVED" if successful_retry else "RECOVERY_NOT_OBSERVED"
    )
    assert result.recovery_observed is successful_retry


def test_rate_limit_workload_failure_keeps_existing_precedence() -> None:
    result = analyze(
        rate_limit_scenario(),
        events(
            [
                FaultInjectedPayload(
                    operation_id="failed-operation",
                    fault_type="http_rate_limit",
                    parameters={"retry_after_seconds": 1},
                ),
                OperationFailedPayload(
                    operation_id="failed-operation",
                    fingerprint="fingerprint",
                    failure_kind="injected_rate_limit",
                    status_code=429,
                    fault_related=True,
                ),
                WorkloadCompletedPayload(
                    exit_code=1,
                    timed_out=False,
                    interrupted=False,
                    duration_ms=1,
                ),
            ]
        ),
    )

    assert result.result == ExperimentResult.FAILED
    assert result.reason_code == "WORKLOAD_EXIT_CODE_MISMATCH"


@pytest.mark.parametrize(
    ("include_success", "expected_result", "reason_code"),
    [
        (True, ExperimentResult.RECOVERED, "RECOVERY_OBSERVED"),
        (False, ExperimentResult.FAILED, "RECOVERY_NOT_OBSERVED"),
    ],
)
def test_malformed_json_recovery_requires_a_successful_linked_retry(
    include_success: bool,
    expected_result: ExperimentResult,
    reason_code: str,
) -> None:
    payloads: list[EventPayload] = [
        FaultInjectedPayload(
            operation_id="failed-operation",
            fault_type="http_malformed_json",
            parameters={},
        ),
        OperationFailedPayload(
            operation_id="failed-operation",
            fingerprint="fingerprint",
            failure_kind="injected_malformed_json",
            status_code=200,
            fault_related=True,
        ),
        RetryObservedPayload(
            operation_id="retry-operation",
            retry_of_operation_id="failed-operation",
            fingerprint="fingerprint",
            attempt=2,
        ),
    ]
    if include_success:
        payloads.append(
            OperationSucceededPayload(
                operation_id="retry-operation",
                fingerprint="fingerprint",
                status_code=200,
                duration_ms=1,
                fault_related=True,
            )
        )
    payloads.append(
        WorkloadCompletedPayload(exit_code=0, timed_out=False, interrupted=False, duration_ms=1)
    )

    result = analyze(scenario(), events(payloads))

    assert result.result == expected_result
    assert result.reason_code == reason_code


def test_plural_recovery_evidence_is_ordered_and_complete() -> None:
    result = analyze_schedule(
        (2, 4),
        [
            FaultInjectedPayload(
                operation_id="failed-one",
                fault_type="http_error",
                parameters={"status_code": 503},
            ),
            OperationFailedPayload(
                operation_id="failed-one",
                fingerprint="fingerprint",
                failure_kind="injected_http_error",
                status_code=503,
                fault_related=True,
            ),
            RetryObservedPayload(
                operation_id="retry-one",
                retry_of_operation_id="failed-one",
                fingerprint="fingerprint",
                attempt=2,
            ),
            OperationSucceededPayload(
                operation_id="retry-one",
                fingerprint="fingerprint",
                status_code=200,
                duration_ms=1,
                fault_related=True,
            ),
            FaultInjectedPayload(
                operation_id="failed-two",
                fault_type="http_error",
                parameters={"status_code": 503},
            ),
            OperationFailedPayload(
                operation_id="failed-two",
                fingerprint="fingerprint",
                failure_kind="injected_http_error",
                status_code=503,
                fault_related=True,
            ),
            RetryObservedPayload(
                operation_id="retry-two",
                retry_of_operation_id="failed-two",
                fingerprint="fingerprint",
                attempt=2,
            ),
            OperationSucceededPayload(
                operation_id="retry-two",
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
        ],
    )

    assert result.result == ExperimentResult.RECOVERED
    assert result.reason_code == "RECOVERY_OBSERVED"
    assert result.scheduled_occurrences == (2, 4)
    assert result.completed_occurrences == (2, 4)
    assert result.recoveries_required == 2
    assert result.recoveries_successful == 2
    assert [row.failed_operation_id for row in result.recovery_evidence] == [
        "failed-one",
        "failed-two",
    ]
    assert [row.retry_operation_id for row in result.recovery_evidence] == [
        "retry-one",
        "retry-two",
    ]
    assert [row.recovery_latency_ms for row in result.recovery_evidence] == [20, 20]


def test_complete_schedule_with_partial_recovery_fails_with_counts() -> None:
    result = analyze_schedule(
        (2, 4),
        [
            FaultInjectedPayload(
                operation_id="failed-one",
                fault_type="http_error",
                parameters={"status_code": 503},
            ),
            OperationFailedPayload(
                operation_id="failed-one",
                fingerprint="fingerprint",
                failure_kind="injected_http_error",
                status_code=503,
                fault_related=True,
            ),
            RetryObservedPayload(
                operation_id="retry-one",
                retry_of_operation_id="failed-one",
                fingerprint="fingerprint",
                attempt=2,
            ),
            OperationSucceededPayload(
                operation_id="retry-one",
                fingerprint="fingerprint",
                status_code=200,
                duration_ms=1,
                fault_related=True,
            ),
            FaultInjectedPayload(
                operation_id="failed-two",
                fault_type="http_error",
                parameters={"status_code": 503},
            ),
            OperationFailedPayload(
                operation_id="failed-two",
                fingerprint="fingerprint",
                failure_kind="injected_http_error",
                status_code=503,
                fault_related=True,
            ),
            WorkloadCompletedPayload(
                exit_code=0,
                timed_out=False,
                interrupted=False,
                duration_ms=1,
            ),
        ],
    )

    assert result.result == ExperimentResult.FAILED
    assert result.reason_code == "RECOVERY_NOT_OBSERVED"
    assert result.recoveries_required == 2
    assert result.recoveries_successful == 1
    assert result.diagnostics == (
        "2 recoveries were required, but 1 successful recoveries were observed",
    )


def test_mixed_tolerated_and_recovered_injections_are_recovered() -> None:
    result = analyze_schedule(
        (2, 4),
        [
            FaultInjectedPayload(
                operation_id="tolerated",
                fault_type="http_latency",
                parameters={"latency_ms": 10},
            ),
            OperationSucceededPayload(
                operation_id="tolerated",
                fingerprint="tolerated-fingerprint",
                status_code=200,
                duration_ms=10,
                fault_related=True,
            ),
            FaultInjectedPayload(
                operation_id="failed",
                fault_type="http_latency",
                parameters={"latency_ms": 10},
            ),
            OperationFailedPayload(
                operation_id="failed",
                fingerprint="failed-fingerprint",
                failure_kind="client_timeout_inferred",
                fault_related=True,
            ),
            RetryObservedPayload(
                operation_id="retry",
                retry_of_operation_id="failed",
                fingerprint="failed-fingerprint",
                attempt=2,
            ),
            OperationSucceededPayload(
                operation_id="retry",
                fingerprint="failed-fingerprint",
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
        ],
    )

    assert result.result == ExperimentResult.RECOVERED
    assert result.reason_code == "RECOVERY_OBSERVED"
    assert result.recoveries_required == 1
    assert result.recoveries_successful == 1


def test_partial_schedule_precedes_successful_recovery() -> None:
    result = analyze_schedule(
        (2, 4),
        [
            FaultInjectedPayload(
                operation_id="failed",
                fault_type="http_error",
                parameters={"status_code": 503},
            ),
            OperationFailedPayload(
                operation_id="failed",
                fingerprint="fingerprint",
                failure_kind="injected_http_error",
                status_code=503,
                fault_related=True,
            ),
            RetryObservedPayload(
                operation_id="retry",
                retry_of_operation_id="failed",
                fingerprint="fingerprint",
                attempt=2,
            ),
            OperationSucceededPayload(
                operation_id="retry",
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
        ],
    )

    assert result.result == ExperimentResult.FAILED
    assert result.reason_code == "FAULT_SCHEDULE_INCOMPLETE"
    assert result.completed_occurrences == (2,)
    assert result.recoveries_successful == 1
    assert result.diagnostics == (
        "the fault schedule configured 2 injections, but only 1 completed",
    )


def test_over_injection_is_an_internal_error() -> None:
    result = analyze_schedule(
        (2, 4),
        [
            FaultInjectedPayload(
                operation_id=f"operation-{index}",
                fault_type="http_latency",
                parameters={"latency_ms": 10},
            )
            for index in range(3)
        ]
        + [
            WorkloadCompletedPayload(
                exit_code=0,
                timed_out=False,
                interrupted=False,
                duration_ms=1,
            )
        ],
    )

    assert result.result == ExperimentResult.FAILED
    assert result.reason_code == "INTERNAL_ERROR"
    assert result.completed_occurrences == (2, 4)
