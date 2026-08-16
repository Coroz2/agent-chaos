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
class RecoveryEvidence:
    failed_operation_id: str
    retry_operation_id: str | None
    recovery_latency_ms: int | None


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
    scheduled_occurrences: tuple[int, ...]
    completed_occurrences: tuple[int, ...]
    recovery_evidence: tuple[RecoveryEvidence, ...]

    @property
    def recoveries_required(self) -> int:
        return len(self.recovery_evidence)

    @property
    def recoveries_successful(self) -> int:
        return sum(row.retry_operation_id is not None for row in self.recovery_evidence)

    @property
    def recovery_observed(self) -> bool:
        return self.recoveries_required > 0 and (
            self.recoveries_successful == self.recoveries_required
        )

    # Compatibility conveniences for callers that consumed the v0.3 single-recovery analysis.
    @property
    def failed_operation_id(self) -> str | None:
        return self.recovery_evidence[0].failed_operation_id if self.recovery_evidence else None

    @property
    def retry_operation_id(self) -> str | None:
        if not self.recovery_evidence:
            return None
        return self.recovery_evidence[0].retry_operation_id

    @property
    def recovery_latency_ms(self) -> int | None:
        if not self.recovery_evidence:
            return None
        return self.recovery_evidence[0].recovery_latency_ms


def analyze(scenario: Scenario, events: tuple[Event, ...]) -> AnalysisResult:
    """Derive metrics and the outcome exclusively from structured events."""
    ordered_events = tuple(sorted(events, key=lambda event: event.sequence))
    event_sequences = [event.sequence for event in ordered_events]
    duplicate_event_sequence = len(event_sequences) != len(set(event_sequences))
    by_type: dict[EventType, list[Event]] = {event_type: [] for event_type in EventType}
    for event in ordered_events:
        by_type[event.event_type].append(event)

    workload_payload = _last_payload(
        by_type[EventType.WORKLOAD_COMPLETED], WorkloadCompletedPayload
    )
    run_error = _last_payload(by_type[EventType.RUN_ERROR], RunErrorPayload)
    injected = [
        (event, event.payload)
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
    scheduled_occurrences = _configured_schedule(scenario)
    completed_occurrences = scheduled_occurrences[: min(len(injected), len(scheduled_occurrences))]
    fault_failures = [(event, payload) for event, payload in failures if payload.fault_related]
    recovery_evidence = _build_recovery_evidence(fault_failures, retries, successes)
    recoveries_required = len(recovery_evidence)
    recoveries_successful = sum(row.retry_operation_id is not None for row in recovery_evidence)
    injected_operation_ids = [payload.operation_id for _, payload in injected]
    duplicate_injection = len(injected_operation_ids) != len(set(injected_operation_ids))

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
    elif duplicate_event_sequence:
        reason_code = "INTERNAL_ERROR"
        diagnostics.append("duplicate event sequence numbers were recorded")
    elif scenario.fault is None:
        result = ExperimentResult.PASSED
        reason_code = "BASELINE_SUCCEEDED"
    elif not injected:
        reason_code = "FAULT_NOT_TRIGGERED"
        diagnostics.append("the configured fault occurrence was never reached")
    elif duplicate_injection:
        reason_code = "INTERNAL_ERROR"
        diagnostics.append("duplicate fault injection evidence was recorded for one operation")
    elif len(injected) > len(scheduled_occurrences):
        reason_code = "INTERNAL_ERROR"
        diagnostics.append(
            f"the fault schedule configured {len(scheduled_occurrences)} injections, "
            f"but {len(injected)} were recorded"
        )
    elif len(injected) < len(scheduled_occurrences):
        reason_code = "FAULT_SCHEDULE_INCOMPLETE"
        diagnostics.append(
            f"the fault schedule configured {len(scheduled_occurrences)} injections, "
            f"but only {len(injected)} completed"
        )
    else:
        if not fault_failures:
            result = ExperimentResult.PASSED
            reason_code = "FAULT_TOLERATED"
        elif recoveries_successful < recoveries_required:
            reason_code = "RECOVERY_NOT_OBSERVED"
            diagnostics.append(
                f"{recoveries_required} recoveries were required, "
                f"but {recoveries_successful} successful recoveries were observed"
            )
        else:
            result = ExperimentResult.RECOVERED
            reason_code = "RECOVERY_OBSERVED"

    duration_ms = ordered_events[-1].elapsed_ms if ordered_events else 0
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
        scheduled_occurrences=scheduled_occurrences,
        completed_occurrences=completed_occurrences,
        recovery_evidence=recovery_evidence,
    )


def _configured_schedule(scenario: Scenario) -> tuple[int, ...]:
    if scenario.fault is None:
        return ()
    return scenario.fault.trigger.schedule


def _build_recovery_evidence(
    failures: list[tuple[Event, OperationFailedPayload]],
    retries: list[tuple[Event, RetryObservedPayload]],
    successes: list[tuple[Event, OperationSucceededPayload]],
) -> tuple[RecoveryEvidence, ...]:
    successes_by_operation: dict[str, list[tuple[Event, OperationSucceededPayload]]] = {}
    for event, payload in successes:
        successes_by_operation.setdefault(payload.operation_id, []).append((event, payload))

    used_successful_operations: set[str] = set()
    evidence: list[RecoveryEvidence] = []
    for failure_event, failure_payload in failures:
        retry_operation_id: str | None = None
        recovery_latency_ms: int | None = None
        for retry_event, retry_payload in retries:
            if (
                retry_payload.retry_of_operation_id != failure_payload.operation_id
                or retry_event.sequence <= failure_event.sequence
                or retry_payload.fingerprint != failure_payload.fingerprint
                or retry_payload.operation_id in used_successful_operations
            ):
                continue
            successful = next(
                (
                    (candidate_event, candidate_payload)
                    for candidate_event, candidate_payload in successes_by_operation.get(
                        retry_payload.operation_id, []
                    )
                    if candidate_event.sequence > retry_event.sequence
                    and candidate_payload.fingerprint == failure_payload.fingerprint
                    and candidate_payload.status_code < 400
                ),
                None,
            )
            if successful is None:
                continue
            success_event, _ = successful
            retry_operation_id = retry_payload.operation_id
            recovery_latency_ms = max(0, success_event.elapsed_ms - failure_event.elapsed_ms)
            used_successful_operations.add(retry_payload.operation_id)
            break
        evidence.append(
            RecoveryEvidence(
                failed_operation_id=failure_payload.operation_id,
                retry_operation_id=retry_operation_id,
                recovery_latency_ms=recovery_latency_ms,
            )
        )
    return tuple(evidence)


def _last_payload[T](events: list[Event], payload_type: type[T]) -> T | None:
    for event in reversed(events):
        if isinstance(event.payload, payload_type):
            return event.payload
    return None
