"""Shared text rendering for experiment reports."""

from pathlib import Path

from agentchaos.reporting.models import (
    FaultReportV2,
    RecoveryReport,
    RecoveryReportV2,
    ReportDocument,
)


def render_summary(report: ReportDocument, report_path: Path) -> str:
    """Render the canonical result summary for a validated report."""
    if report.schema_version == 1:
        assert isinstance(report.recovery, RecoveryReport)
        fault_line = str(report.faults_injected)
        recovery_lines = f"Successful recovery:   {'yes' if report.recovery.observed else 'no'}\n"
    else:
        assert isinstance(report.fault, FaultReportV2)
        assert isinstance(report.recovery, RecoveryReportV2)
        fault_line = f"{report.faults_injected}/{len(report.fault.scheduled_occurrences)}"
        recovery_lines = (
            f"Successful recoveries: {report.recovery.successful}/{report.recovery.required}\n"
        )
    return (
        "\nExperiment complete.\n\n"
        f"Result: {report.result.value}\n"
        f"Reason: {report.reason_code}\n\n"
        f"Faults injected:       {fault_line}\n"
        f"Failed operations:     {report.failed_operations}\n"
        f"Retries:               {report.retries_observed}\n"
        f"{recovery_lines}"
        f"Duration:              {report.duration_ms / 1000:.2f}s\n"
        f"\nReport:\n{report_path}\n"
    )
