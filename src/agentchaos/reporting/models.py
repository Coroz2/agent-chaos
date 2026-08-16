"""Versioned report models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentchaos.analysis.analyzer import ExperimentResult


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkloadReport(ReportModel):
    name: str
    expected_exit_code: int
    exit_code: int | None
    timed_out: bool
    interrupted: bool


class FaultReport(ReportModel):
    configured: bool
    type: str | None
    injected: bool


class RecoveryReport(ReportModel):
    observed: bool
    failed_operation_id: str | None
    retry_operation_id: str | None
    recovery_latency_ms: int | None


class TimingReport(ReportModel):
    started_at: str
    completed_at: str


class ArtifactReport(ReportModel):
    scenario: str
    events: str
    stdout: str
    stderr: str
    dependency_stdout: str | None
    dependency_stderr: str | None
    report: str


class FaultReportV2(ReportModel):
    configured: bool
    type: str | None
    injected: bool
    scheduled_occurrences: tuple[int, ...]
    completed_occurrences: tuple[int, ...]
    schedule_completed: bool

    @model_validator(mode="after")
    def validate_schedule(self) -> FaultReportV2:
        scheduled = self.scheduled_occurrences
        completed = self.completed_occurrences
        if any(value <= 0 for value in scheduled) or any(
            current >= following
            for current, following in zip(scheduled, scheduled[1:], strict=False)
        ):
            raise ValueError("scheduled occurrences must be positive and strictly increasing")
        if completed != scheduled[: len(completed)]:
            raise ValueError("completed occurrences must be a prefix of the schedule")
        if self.injected != bool(completed):
            raise ValueError("injected must reflect completed occurrences")
        if self.schedule_completed != (completed == scheduled):
            raise ValueError("schedule_completed must reflect schedule completion")
        if self.configured:
            if self.type is None or not scheduled:
                raise ValueError("configured faults require a type and non-empty schedule")
        elif self.type is not None or scheduled or completed or not self.schedule_completed:
            raise ValueError("baseline fault reports must use the empty completed schedule")
        return self


class RecoveryEvidenceReport(ReportModel):
    failed_operation_id: str
    successful_retry_operation_id: str | None
    recovery_latency_ms: int | None = Field(ge=0)

    @model_validator(mode="after")
    def validate_success_fields(self) -> RecoveryEvidenceReport:
        if (self.successful_retry_operation_id is None) != (self.recovery_latency_ms is None):
            raise ValueError("successful retry and recovery latency must be present together")
        return self


class RecoveryReportV2(ReportModel):
    required: int = Field(ge=0)
    successful: int = Field(ge=0)
    evidence: tuple[RecoveryEvidenceReport, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> RecoveryReportV2:
        if self.required != len(self.evidence):
            raise ValueError("required must equal the number of recovery evidence rows")
        successful = [
            row.successful_retry_operation_id
            for row in self.evidence
            if row.successful_retry_operation_id is not None
        ]
        if self.successful != len(successful):
            raise ValueError("successful must equal the number of resolved evidence rows")
        failed_ids = [row.failed_operation_id for row in self.evidence]
        if len(failed_ids) != len(set(failed_ids)):
            raise ValueError("failed operation IDs must be unique")
        if len(successful) != len(set(successful)):
            raise ValueError("successful retry operation IDs must be unique")
        return self


class Report(ReportModel):
    """Strict versioned report with schema-1 construction compatibility."""

    schema_version: Literal[1, 2] = 1
    run_id: str
    scenario_name: str
    result: ExperimentResult
    reason_code: str
    duration_ms: int
    faults_injected: int
    operations_observed: int
    successful_operations: int
    failed_operations: int
    retries_observed: int
    workload_exit_code: int | None
    workload: WorkloadReport
    fault: FaultReport | FaultReportV2
    recovery: RecoveryReport | RecoveryReportV2
    timing: TimingReport
    artifacts: ArtifactReport
    diagnostics: list[str]

    @model_validator(mode="after")
    def validate_versioned_shape(self) -> Report:
        if self.schema_version == 1:
            if not isinstance(self.fault, FaultReport) or not isinstance(
                self.recovery, RecoveryReport
            ):
                raise ValueError("schema-1 reports require schema-1 fault and recovery objects")
        elif not isinstance(self.fault, FaultReportV2) or not isinstance(
            self.recovery, RecoveryReportV2
        ):
            raise ValueError("schema-2 reports require schema-2 fault and recovery objects")
        elif self.faults_injected != len(self.fault.completed_occurrences):
            raise ValueError("faults_injected must equal completed scheduled occurrences")
        return self


type ReportDocument = Report
