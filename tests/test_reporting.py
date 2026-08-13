from pathlib import Path

from agentchaos.analysis.analyzer import ExperimentResult
from agentchaos.reporting.models import (
    ArtifactReport,
    FaultReport,
    RecoveryReport,
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
