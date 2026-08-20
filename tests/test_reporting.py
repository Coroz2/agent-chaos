import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.reporting.models import (
    ArtifactReport,
    FaultReport,
    FaultReportV2,
    FaultReportV3,
    OccurrenceScheduleTriggerReport,
    ProbabilityWindowReport,
    RecoveryEvidenceReport,
    RecoveryReport,
    RecoveryReportV2,
    Report,
    SeededProbabilityTriggerReport,
    TimingReport,
    WorkloadReport,
)
from agentchaos.reporting.writer import write_report


def test_report_is_written_atomically(tmp_path: Path) -> None:
    report = Report(
        run_id="run",
        scenario_name="scenario",
        result=ExperimentResult.PASSED,
        reason_code="BASELINE_SUCCEEDED",
        duration_ms=10,
        faults_injected=0,
        operations_observed=1,
        successful_operations=1,
        failed_operations=0,
        retries_observed=0,
        workload_exit_code=0,
        workload=WorkloadReport(
            name="agent", expected_exit_code=0, exit_code=0, timed_out=False, interrupted=False
        ),
        fault=FaultReport(configured=False, type=None, injected=False),
        recovery=RecoveryReport(
            observed=False,
            failed_operation_id=None,
            retry_operation_id=None,
            recovery_latency_ms=None,
        ),
        timing=TimingReport(started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z"),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout=None,
            dependency_stderr=None,
            report="report.json",
        ),
        diagnostics=[],
    )
    path = tmp_path / "report.json"

    write_report(report, path)

    assert Report.model_validate_json(path.read_text(encoding="utf-8")) == report
    assert not (tmp_path / "report.json.tmp").exists()


def test_schema_two_report_is_written_and_loaded_through_exported_report(tmp_path: Path) -> None:
    report = schema_two_report()
    path = tmp_path / "report.json"

    write_report(report, path)

    loaded = Report.model_validate_json(path.read_text(encoding="utf-8"), strict=True)
    assert loaded == report
    assert loaded.schema_version == 2
    assert isinstance(loaded.fault, FaultReportV2)
    assert isinstance(loaded.recovery, RecoveryReportV2)


def test_report_versions_reject_mixed_nested_shapes() -> None:
    report = schema_two_report()

    with pytest.raises(ValidationError):
        validate_json_payload(report.model_dump(mode="json") | {"schema_version": 1})


def test_schema_two_report_preserves_over_injection_internal_error() -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload |= {
        "result": "FAILED",
        "reason_code": "INTERNAL_ERROR",
        "faults_injected": 2,
    }

    report = validate_json_payload(payload)

    assert report.faults_injected == 2


def test_schema_two_report_rejects_unexplained_injection_count_mismatch() -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload["faults_injected"] = 2

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_two_baseline_rejects_recovery_evidence() -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload["faults_injected"] = 0
    payload["fault"] = {
        "configured": False,
        "type": None,
        "injected": False,
        "scheduled_occurrences": [],
        "completed_occurrences": [],
        "schedule_completed": True,
    }

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_two_baseline_rejects_over_injection_exception() -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload |= {
        "result": "FAILED",
        "reason_code": "INTERNAL_ERROR",
        "faults_injected": 1,
        "fault": {
            "configured": False,
            "type": None,
            "injected": False,
            "scheduled_occurrences": [],
            "completed_occurrences": [],
            "schedule_completed": True,
        },
        "recovery": {"required": 0, "successful": 0, "evidence": []},
    }

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"failed_operations": 0},
        {"retries_observed": 0},
        {"successful_operations": 0},
        {"operations_observed": -1},
        {"operations_observed": 1},
    ],
)
def test_schema_two_report_rejects_impossible_aggregate_counts(
    change: dict[str, object],
) -> None:
    payload = schema_two_report().model_dump(mode="json") | change

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_two_recovery_count_cannot_exceed_fault_injections() -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload |= {
        "result": "FAILED",
        "reason_code": "FAULT_NOT_TRIGGERED",
        "faults_injected": 0,
        "fault": {
            "configured": True,
            "type": "http_error",
            "injected": False,
            "scheduled_occurrences": [2],
            "completed_occurrences": [],
            "schedule_completed": False,
        },
    }

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


@pytest.mark.parametrize("case", ["incomplete", "partial", "zero-required"])
def test_schema_two_report_rejects_outcome_that_contradicts_evidence(case: str) -> None:
    payload = schema_two_report().model_dump(mode="json")
    if case == "incomplete":
        payload["fault"] |= {
            "scheduled_occurrences": [1, 2],
            "completed_occurrences": [1],
            "schedule_completed": False,
        }
    elif case == "partial":
        payload["recovery"] = {
            "required": 1,
            "successful": 0,
            "evidence": [
                {
                    "failed_operation_id": "failed",
                    "retry_operation_id": None,
                    "recovery_latency_ms": None,
                }
            ],
        }
    else:
        payload |= {"failed_operations": 0}
        payload["recovery"] = {"required": 0, "successful": 0, "evidence": []}

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


@pytest.mark.parametrize(
    "case",
    ["top-level-exit", "wrong-exit", "timed-out", "interrupted", "both-terminal-flags"],
)
def test_schema_two_report_rejects_outcome_that_contradicts_workload(case: str) -> None:
    payload = schema_two_report().model_dump(mode="json")
    if case == "top-level-exit":
        payload["workload_exit_code"] = 1
    elif case == "wrong-exit":
        payload["workload_exit_code"] = 1
        payload["workload"]["exit_code"] = 1
    elif case == "timed-out":
        payload["workload"]["timed_out"] = True
    elif case == "interrupted":
        payload["workload"]["interrupted"] = True
    else:
        payload["workload"]["timed_out"] = True
        payload["workload"]["interrupted"] = True

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"required": 3},
        {"successful": 0},
        {"unknown": "value"},
        {
            "evidence": [
                {
                    "failed_operation_id": "failed",
                    "retry_operation_id": "retry",
                    "recovery_latency_ms": None,
                }
            ]
        },
    ],
)
def test_schema_two_recovery_evidence_is_strict(change: dict[str, object]) -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload["recovery"] |= change

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"completed_occurrences": [4]},
        {"schedule_completed": False},
        {"injected": False},
        {"scheduled_occurrences": [4, 2]},
        {"unknown": "value"},
    ],
)
def test_schema_two_fault_schedule_is_strict(change: dict[str, object]) -> None:
    payload = schema_two_report().model_dump(mode="json")
    payload["fault"] |= change

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_three_probability_report_round_trips_strictly(tmp_path: Path) -> None:
    report = schema_three_probability_report()
    path = tmp_path / "report.json"

    write_report(report, path)

    loaded = Report.model_validate_json(path.read_bytes(), strict=True)
    assert loaded == report
    assert loaded.schema_version == 3
    assert isinstance(loaded.fault, FaultReportV3)
    assert isinstance(loaded.fault.trigger, SeededProbabilityTriggerReport)


@pytest.mark.parametrize(
    "change",
    [
        {"probability": 0},
        {"probability": 0.1234567},
        {"seed": -1},
        {"evaluated_occurrences": 6},
        {"selected_occurrences": [1, 2]},
        {"selected_occurrences": [4, 2]},
        {"completed": False},
        {"unknown": True},
    ],
)
def test_schema_three_probability_evidence_is_strict(change: dict[str, object]) -> None:
    payload = schema_three_probability_report().model_dump(mode="json")
    payload["fault"]["trigger"] |= change

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_three_rejects_hash_selection_contradiction() -> None:
    payload = schema_three_probability_report().model_dump(mode="json")
    payload["fault"]["trigger"]["selected_occurrences"] = [2]
    payload["faults_injected"] = 1

    with pytest.raises(ValidationError):
        validate_json_payload(payload)


def test_schema_three_preserves_inconsistent_evidence_for_internal_error() -> None:
    payload = schema_three_probability_report().model_dump(mode="json")
    payload |= {"result": "FAILED", "reason_code": "INTERNAL_ERROR", "faults_injected": 1}
    payload["fault"]["trigger"]["selected_occurrences"] = [2]
    payload["failed_operations"] = 1
    payload["recovery"] = {
        "required": 1,
        "successful": 1,
        "evidence": [payload["recovery"]["evidence"][0]],
    }

    report = validate_json_payload(payload)

    assert report.reason_code == "INTERNAL_ERROR"


def test_schema_three_preserves_out_of_window_injection_count_for_internal_error() -> None:
    payload = schema_three_probability_report().model_dump(mode="json")
    payload |= {"result": "FAILED", "reason_code": "INTERNAL_ERROR", "faults_injected": 1}
    payload["fault"]["trigger"]["selected_occurrences"] = []
    payload["failed_operations"] = 1
    payload["recovery"] = {
        "required": 1,
        "successful": 1,
        "evidence": [payload["recovery"]["evidence"][0]],
    }

    report = validate_json_payload(payload)

    assert report.faults_injected == 1


def test_schema_three_occurrence_schedule_uses_generalized_trigger() -> None:
    report = schema_three_probability_report().model_copy(
        update={
            "fault": FaultReportV3(
                configured=True,
                type="http_error",
                injected=True,
                trigger=OccurrenceScheduleTriggerReport(
                    scheduled_occurrences=(2, 4),
                    completed_occurrences=(2, 4),
                    completed=True,
                ),
            )
        }
    )

    loaded = validate_json_payload(report.model_dump(mode="json"))

    assert isinstance(loaded.fault, FaultReportV3)
    assert isinstance(loaded.fault.trigger, OccurrenceScheduleTriggerReport)


def validate_json_payload(payload: dict[str, object]) -> Report:
    return Report.model_validate_json(json.dumps(payload), strict=True)


def schema_two_report() -> Report:
    return Report(
        schema_version=2,
        run_id="run",
        scenario_name="scenario",
        result=ExperimentResult.RECOVERED,
        reason_code="RECOVERY_OBSERVED",
        duration_ms=20,
        faults_injected=1,
        operations_observed=2,
        successful_operations=1,
        failed_operations=1,
        retries_observed=1,
        workload_exit_code=0,
        workload=WorkloadReport(
            name="agent", expected_exit_code=0, exit_code=0, timed_out=False, interrupted=False
        ),
        fault=FaultReportV2(
            configured=True,
            type="http_error",
            injected=True,
            scheduled_occurrences=(2,),
            completed_occurrences=(2,),
            schedule_completed=True,
        ),
        recovery=RecoveryReportV2(
            required=1,
            successful=1,
            evidence=(
                RecoveryEvidenceReport(
                    failed_operation_id="failed",
                    retry_operation_id="retry",
                    recovery_latency_ms=10,
                ),
            ),
        ),
        timing=TimingReport(started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z"),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout=None,
            dependency_stderr=None,
            report="report.json",
        ),
        diagnostics=[],
    )


def schema_three_probability_report() -> Report:
    return Report(
        schema_version=3,
        run_id="run",
        scenario_name="probability-scenario",
        result=ExperimentResult.RECOVERED,
        reason_code="RECOVERY_OBSERVED",
        duration_ms=20,
        faults_injected=2,
        operations_observed=5,
        successful_operations=3,
        failed_operations=2,
        retries_observed=2,
        workload_exit_code=0,
        workload=WorkloadReport(
            name="agent", expected_exit_code=0, exit_code=0, timed_out=False, interrupted=False
        ),
        fault=FaultReportV3(
            configured=True,
            type="http_error",
            injected=True,
            trigger=SeededProbabilityTriggerReport(
                probability=0.5,
                seed=10,
                window=ProbabilityWindowReport(start_occurrence=1, end_occurrence=5),
                evaluated_occurrences=5,
                selected_occurrences=(2, 4),
                completed=True,
            ),
        ),
        recovery=RecoveryReportV2(
            required=2,
            successful=2,
            evidence=(
                RecoveryEvidenceReport(
                    failed_operation_id="failed-one",
                    retry_operation_id="retry-one",
                    recovery_latency_ms=10,
                ),
                RecoveryEvidenceReport(
                    failed_operation_id="failed-two",
                    retry_operation_id="retry-two",
                    recovery_latency_ms=10,
                ),
            ),
        ),
        timing=TimingReport(started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z"),
        artifacts=ArtifactReport(
            scenario="scenario.yaml",
            events="events.jsonl",
            stdout="stdout.log",
            stderr="stderr.log",
            dependency_stdout=None,
            dependency_stderr=None,
            report="report.json",
        ),
        diagnostics=[],
    )
