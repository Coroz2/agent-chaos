from pathlib import Path

import pytest
from pydantic import ValidationError

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.reporting.models import (
    ArtifactReport,
    FaultReport,
    FaultReportV2,
    RecoveryEvidenceReport,
    RecoveryReport,
    RecoveryReportV2,
    Report,
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
        Report.model_validate(
            report.model_dump(mode="json")
            | {
                "schema_version": 1,
            },
            strict=True,
        )


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
                    "successful_retry_operation_id": "retry",
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
        Report.model_validate(payload, strict=True)


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
        Report.model_validate(payload, strict=True)


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
                    successful_retry_operation_id="retry",
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
