"""Versioned report models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

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


class Report(ReportModel):
    schema_version: Literal[1] = 1
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
    fault: FaultReport
    recovery: RecoveryReport
    timing: TimingReport
    artifacts: ArtifactReport
    diagnostics: list[str]
