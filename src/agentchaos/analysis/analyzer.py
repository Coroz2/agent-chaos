"""Explicit, event-driven experiment outcome analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentchaos.config.models import Scenario
from agentchaos.events.models import (
    Event,
    EventType,
    FaultInjectedPayload,
    OperationFailedPayload,
    OperationSucceededPayload,
    RetryObservedPayload,
    RunErrorPayload,
    WorkloadCompletedPayload,
)


class ExperimentResult(StrEnum):
    PASSED = "PASSED"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    result: ExperimentResult
    reason_code: str
    diagnostics: tuple[str, ...]
    duration_ms: int
    faults_injected: int
    operations_observed: int
    successful_operations: int
    failed_operations: int
    retries_observed: int
    workload_exit_code: int | None
    workload_timed_out: bool
    workload_interrupted: bool
    recovery_observed: bool
    failed_operation_id: str | None
    retry_operation_id: str | None
    recovery_latency_ms: int | None


def analyze(scenario: Scenario, events: tuple[Event, ...]) -> AnalysisResult:
    """Derive metrics and the outcome exclusively from structured events."""
    by_type: dict[EventType, list[Event]] = {event_type: [] for event_type in EventType}
    for event in events:
        by_type[event.event_type].append(event)

    workload_payload = _last_payload(
        by_type[EventType.WORKLOAD_COMPLETED], WorkloadCompletedPayload
    )
    run_error = _last_payload(by_type[EventType.RUN_ERROR], RunErrorPayload)
    injected = [
        event.payload
        for event in by_type[EventType.FAULT_INJECTED]
        if isinstance(event.payload, FaultInjectedPayload)
    ]
    failures = [
        (event, event.payload)
        for event in by_type[EventType.OPERATION_FAILED]
        if isinstance(event.payload, OperationFailedPayload)
    ]
    successes = [
        (event, event.payload)
        for event in by_type[EventType.OPERATION_SUCCEEDED]
        if isinstance(event.payload, OperationSucceededPayload)
    ]
    retries = [
        (event, event.payload)
        for event in by_type[EventType.RETRY_OBSERVED]
        if isinstance(event.payload, RetryObservedPayload)
    ]

    result = ExperimentResult.FAILED
    reason_code = "INTERNAL_ERROR"
    diagnostics: list[str] = []
    recovery_observed = False
    failed_operation_id: str | None = None
    retry_operation_id: str | None = None
    recovery_latency_ms: int | None = None

    if run_error is not None:
        reason_code = run_error.reason_code
        diagnostics.append(run_error.message)
    elif workload_payload is None:
        reason_code = "WORKLOAD_NOT_COMPLETED"
        diagnostics.append("workload completion was not recorded")
    elif workload_payload.interrupted:
        reason_code = "INTERRUPTED"
        diagnostics.append("the workload was interrupted")
    elif workload_payload.timed_out:
        reason_code = "WORKLOAD_TIMED_OUT"
        diagnostics.append(workload_payload.error or "the workload timed out")
    elif workload_payload.exit_code != scenario.success.exit_code:
        reason_code = "WORKLOAD_EXIT_CODE_MISMATCH"
        diagnostics.append(
            f"expected workload exit code {scenario.success.exit_code}, "
            f"got {workload_payload.exit_code}"
        )
    elif scenario.fault is None:
        result = ExperimentResult.PASSED
        reason_code = "BASELINE_SUCCEEDED"
    elif not injected:
        reason_code = "FAULT_NOT_TRIGGERED"
        diagnostics.append("the configured fault occurrence was never reached")
    else:
        fault_failures = [(event, payload) for event, payload in failures if payload.fault_related]
        if not fault_failures:
            result = ExperimentResult.PASSED
            reason_code = "FAULT_TOLERATED"
        else:
            failure_event, failure_payload = fault_failures[0]
            failed_operation_id = failure_payload.operation_id
            linked_retries = [
                (event, payload)
                for event, payload in retries
                if payload.retry_of_operation_id == failed_operation_id
            ]
            successful_ids = {payload.operation_id: event for event, payload in successes}
            successful_retry = next(
                (
                    (event, payload, successful_ids[payload.operation_id])
                    for event, payload in linked_retries
                    if payload.operation_id in successful_ids
                ),
                None,
            )
            if successful_retry is None:
                reason_code = "RECOVERY_NOT_OBSERVED"
                diagnostics.append("no successful fingerprint-matched retry was observed")
            else:
                _, retry_payload, success_event = successful_retry
                result = ExperimentResult.RECOVERED
                reason_code = "RECOVERY_OBSERVED"
                recovery_observed = True
                retry_operation_id = retry_payload.operation_id
                recovery_latency_ms = max(0, success_event.elapsed_ms - failure_event.elapsed_ms)

    duration_ms = events[-1].elapsed_ms if events else 0
    return AnalysisResult(
        result=result,
        reason_code=reason_code,
        diagnostics=tuple(diagnostics),
        duration_ms=duration_ms,
        faults_injected=len(injected),
        operations_observed=len(by_type[EventType.OPERATION_OBSERVED]),
        successful_operations=len(successes),
        failed_operations=len(failures),
        retries_observed=len(retries),
        workload_exit_code=None if workload_payload is None else workload_payload.exit_code,
        workload_timed_out=False if workload_payload is None else workload_payload.timed_out,
        workload_interrupted=False if workload_payload is None else workload_payload.interrupted,
        recovery_observed=recovery_observed,
        failed_operation_id=failed_operation_id,
        retry_operation_id=retry_operation_id,
        recovery_latency_ms=recovery_latency_ms,
    )


def _last_payload[T](events: list[Event], payload_type: type[T]) -> T | None:
    for event in reversed(events):
        if isinstance(event.payload, payload_type):
            return event.payload
    return None
